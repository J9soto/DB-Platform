import unittest
from dataclasses import dataclass

import yaml

from dbre_platform.config.models import DatabaseRequest
from dbre_platform.provisioning.k3s import _PortForwardTunnel, _resilient, build_cluster_manifest


def make_request(**spec_overrides) -> DatabaseRequest:
    doc = {
        "metadata": {
            "name": "orders-api",
            "environment": "dev",
            "owner": "payments-team",
            "cost_center": "CC-4471",
            "data_classification": "internal",
        },
        "spec": {"platform": "k3s", **spec_overrides},
    }
    return DatabaseRequest.model_validate(doc)


class TestBuildClusterManifest(unittest.TestCase):
    def test_is_yaml_serializable_and_well_shaped(self):
        manifest = build_cluster_manifest(make_request())
        round_tripped = yaml.safe_load(yaml.dump(manifest))
        self.assertEqual(round_tripped["apiVersion"], "postgresql.cnpg.io/v1")
        self.assertEqual(round_tripped["kind"], "Cluster")
        self.assertEqual(round_tripped["metadata"]["name"], "orders-api")

    def test_namespace_defaults_to_spec_then_override(self):
        self.assertEqual(build_cluster_manifest(make_request())["metadata"]["namespace"], "dbre")
        self.assertEqual(
            build_cluster_manifest(make_request(namespace="team-a"))["metadata"]["namespace"],
            "team-a",
        )
        self.assertEqual(
            build_cluster_manifest(make_request(), namespace="forced")["metadata"]["namespace"],
            "forced",
        )

    def test_single_instance_by_default_no_anti_affinity(self):
        spec = build_cluster_manifest(make_request())["spec"]
        self.assertEqual(spec["instances"], 1)
        self.assertNotIn("affinity", spec)

    def test_multi_az_bumps_to_three_instances_with_anti_affinity(self):
        spec = build_cluster_manifest(make_request(multi_az=True))["spec"]
        self.assertEqual(spec["instances"], 3)
        self.assertTrue(spec["affinity"]["enablePodAntiAffinity"])

    def test_explicit_instances_respected_and_never_lowered_by_multi_az(self):
        self.assertEqual(build_cluster_manifest(make_request(instances=2))["spec"]["instances"], 2)
        self.assertEqual(
            build_cluster_manifest(make_request(instances=4, multi_az=True))["spec"]["instances"], 4
        )

    def test_storage_size_and_optional_class(self):
        spec = build_cluster_manifest(make_request(storage_gb=50))["spec"]
        self.assertEqual(spec["storage"]["size"], "50Gi")
        self.assertNotIn("storageClass", spec["storage"])
        spec2 = build_cluster_manifest(make_request(storage_class="fast-ssd"))["spec"]
        self.assertEqual(spec2["storage"]["storageClass"], "fast-ssd")

    def test_image_uses_major_engine_version_tag(self):
        spec = build_cluster_manifest(make_request(engine_version="15"))["spec"]
        self.assertEqual(spec["imageName"], "ghcr.io/cloudnative-pg/postgresql:15")

    def test_shared_preload_libraries_split_out_of_parameters(self):
        # pgaudit must be preloaded, so it lands in the dedicated list, never
        # in spec.postgresql.parameters (CNPG rejects it there).
        spec = build_cluster_manifest(make_request(extensions=["pg_stat_statements", "pgaudit"]))["spec"]
        self.assertIn("pgaudit", spec["postgresql"]["shared_preload_libraries"])
        self.assertNotIn("shared_preload_libraries", spec["postgresql"].get("parameters", {}))

    def test_enhanced_monitoring_toggles_pod_monitor(self):
        self.assertFalse(build_cluster_manifest(make_request())["spec"]["monitoring"]["enablePodMonitor"])
        self.assertTrue(
            build_cluster_manifest(make_request(enhanced_monitoring=True))["spec"]["monitoring"][
                "enablePodMonitor"
            ]
        )

    def test_resources_come_from_spec(self):
        spec = build_cluster_manifest(make_request(resources={"cpu_request": "500m", "memory_limit": "2Gi"}))[
            "spec"
        ]
        self.assertEqual(spec["resources"]["requests"]["cpu"], "500m")
        self.assertEqual(spec["resources"]["limits"]["memory"], "2Gi")

    def test_superuser_access_enabled_for_bootstrap(self):
        self.assertTrue(build_cluster_manifest(make_request())["spec"]["enableSuperuserAccess"])

    def test_required_tags_are_preserved_as_annotations(self):
        annotations = build_cluster_manifest(make_request())["metadata"]["annotations"]
        self.assertEqual(annotations["dbre.platform/tag-application"], "orders-api")
        self.assertEqual(annotations["dbre.platform/tag-managed_by"], "dbre-platform")

    def test_environment_label_present(self):
        labels = build_cluster_manifest(make_request())["metadata"]["labels"]
        self.assertEqual(labels["dbre.platform/environment"], "dev")
        self.assertEqual(labels["app.kubernetes.io/managed-by"], "dbre-platform")


@dataclass
class _FakeResult:
    success: bool
    stderr: str = ""
    stdout: str = ""


class _FakeTunnel:
    def __init__(self):
        self.ensured = 0

    def ensure(self, **_):
        self.ensured += 1


class TestResilient(unittest.TestCase):
    def test_returns_immediately_on_success(self):
        tunnel = _FakeTunnel()
        calls = []

        def call():
            calls.append(1)
            return _FakeResult(success=True)

        result = _resilient(tunnel, call, sleep=lambda _: None)
        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)
        self.assertEqual(tunnel.ensured, 1)

    def test_does_not_retry_a_real_sql_error(self):
        tunnel = _FakeTunnel()
        calls = []

        def call():
            calls.append(1)
            return _FakeResult(success=False, stderr='ERROR: syntax error at or near "FROM"')

        result = _resilient(tunnel, call, sleep=lambda _: None)
        self.assertFalse(result.success)
        self.assertEqual(len(calls), 1)  # not retried -- it's bad SQL, not a dropped tunnel

    def test_retries_a_dropped_tunnel_then_succeeds(self):
        tunnel = _FakeTunnel()
        outcomes = [
            _FakeResult(success=False, stderr="psql: error: connection to server ... Connection refused"),
            _FakeResult(success=False, stderr="server closed the connection unexpectedly"),
            _FakeResult(success=True),
        ]

        result = _resilient(tunnel, lambda: outcomes.pop(0), attempts=5, sleep=lambda _: None)
        self.assertTrue(result.success)
        self.assertEqual(tunnel.ensured, 3)  # re-ensured the tunnel before each attempt

    def test_gives_up_after_attempts_and_returns_last_failure(self):
        tunnel = _FakeTunnel()
        result = _resilient(
            tunnel,
            lambda: _FakeResult(success=False, stderr="Connection refused"),
            attempts=3,
            sleep=lambda _: None,
        )
        self.assertFalse(result.success)
        self.assertEqual(tunnel.ensured, 3)


class TestPortForwardTunnel(unittest.TestCase):
    def test_picks_a_plausible_free_local_port_without_spawning_kubectl(self):
        tunnel = _PortForwardTunnel("dbre", "catalog-api-rw")
        self.assertGreater(tunnel.local_port, 1024)
        self.assertLessEqual(tunnel.local_port, 65535)
        self.assertIsNone(tunnel._proc)  # constructor does not spawn
        tunnel.close()  # no-op when nothing was spawned


if __name__ == "__main__":
    unittest.main()
