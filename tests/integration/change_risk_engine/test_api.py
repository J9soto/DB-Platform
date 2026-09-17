"""End-to-end REST API tests using FastAPI's TestClient -- exercises the
API, pipeline, policy engine, and file-backed store together, which is why
this lives under tests/integration rather than tests/unit (mirrors the
dbre_platform convention of reserving tests/integration for tests that
cross multiple modules)."""

import tempfile
import unittest

from fastapi.testclient import TestClient


class TestChangeRiskEngineApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import change_risk_engine.api.app as app_module
        from change_risk_engine.persistence.store import FileAssessmentStore

        # Point the module-level store at a throwaway directory -- never
        # the repo's own .cre/store -- regardless of import order/cwd.
        app_module._store = FileAssessmentStore(tempfile.mkdtemp())  # noqa: SLF001
        cls.client = TestClient(app_module.app)

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_openapi_schema_is_generated(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("/api/v1/changes", schema["paths"])
        self.assertIn("/api/v1/assessments", schema["paths"])
        self.assertIn("/api/v1/history", schema["paths"])
        self.assertIn("/api/v1/policies", schema["paths"])

    def test_web_ui_served(self):
        response = self.client.get("/ui/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Change Risk Engine", response.text)

    def test_root_redirects_to_ui(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/ui/")

    def test_full_workflow_submit_assess_fetch(self):
        submit = self.client.post(
            "/api/v1/changes",
            json={
                "sql": "ALTER TABLE customer DROP COLUMN credit_score;",
                "environment": "prod",
                "demo": True,
            },
        )
        self.assertEqual(submit.status_code, 201, submit.text)
        change = submit.json()

        assess = self.client.post("/api/v1/assessments", json={"change_id": change["id"]})
        self.assertEqual(assess.status_code, 201, assess.text)
        assessment = assess.json()
        self.assertIn(assessment["risk_level"], ("low", "medium", "high", "critical"))
        self.assertTrue(assessment["requires_approval"])

        fetched = self.client.get(f"/api/v1/assessments/{assessment['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["overall_score"], assessment["overall_score"])

        blast_radius = self.client.get(f"/api/v1/changes/{change['id']}/blast-radius")
        self.assertEqual(blast_radius.status_code, 200)
        self.assertIn("estimated_scope", blast_radius.json())

        recommendations = self.client.get(f"/api/v1/changes/{change['id']}/recommendations")
        self.assertEqual(recommendations.status_code, 200)
        self.assertGreater(len(recommendations.json()), 0)

        history = self.client.get("/api/v1/history")
        self.assertEqual(history.status_code, 200)
        self.assertTrue(any(item["id"] == assessment["id"] for item in history.json()))

    def test_submit_change_without_target_database_or_demo_is_rejected(self):
        response = self.client.post("/api/v1/changes", json={"sql": "SELECT 1;"})
        self.assertEqual(response.status_code, 422)

    def test_get_missing_assessment_returns_404(self):
        response = self.client.get("/api/v1/assessments/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_policies_list(self):
        response = self.client.get("/api/v1/policies")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.json()), 0)

    def test_post_policies_not_supported(self):
        response = self.client.post("/api/v1/policies")
        self.assertEqual(response.status_code, 501)


if __name__ == "__main__":
    unittest.main()
