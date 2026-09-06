# Installing the CloudNativePG operator

K3s mode needs the [CloudNativePG](https://cloudnative-pg.io/) operator
installed in the target cluster **once**. This is a cluster-admin action
(it creates CRDs and a cluster-wide controller), not something the
per-request provisioner does -- the same division of responsibility as
ADR 0006 for AWS networking.

## Pinned version

```
CNPG_VERSION=1.30.0
```

Bump this deliberately, test against `k8s/reference/*.yaml`, and commit
the change. The `k8s-validate` CI job validates the reference manifests
against the CRD schemas, so a breaking CRD change is caught here.

## Install

```bash
kubectl apply --server-side -f \
  https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.0/cnpg-1.30.0.yaml

kubectl -n cnpg-system rollout status deployment/cnpg-controller-manager --timeout=180s
```

Or just:

```bash
make k3s-setup
```

which runs the two commands above and then verifies the
`clusters.postgresql.cnpg.io` CRD is present.

## Uninstall

```bash
kubectl delete -f \
  https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.0/cnpg-1.30.0.yaml
```

Deleting the operator does **not** delete existing `Cluster` resources or
their data; it just stops reconciling them.

## Optional: Prometheus Operator

`spec.enhanced_monitoring: true` makes the generated `Cluster` set
`monitoring.enablePodMonitor: true`, which needs the `PodMonitor` CRD from
the Prometheus Operator (e.g. `kube-prometheus-stack`). If you do not run
Prometheus, leave `enhanced_monitoring` at its default (`false`) -- dev
and staging requests do.
