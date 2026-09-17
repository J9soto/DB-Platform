import unittest

from change_risk_engine.blast_radius.analyzer import BlastRadiusAnalyzer
from change_risk_engine.demo.fixtures import build_demo_database_metadata, load_demo_dependency_config
from change_risk_engine.dependencies.graph_builder import DependencyGraphBuilder, table_node_id
from change_risk_engine.domain.dependency import Dependency, DependencyGraph, DependencyNode
from change_risk_engine.domain.enums import DependencySource, ResourceType


class TestDependencyGraphTraversal(unittest.TestCase):
    """A -> B means "A depends on B" (see DependencyGraph docstring)."""

    def setUp(self):
        self.graph = DependencyGraph()
        for name in ("a", "b", "c"):
            self.graph.add_node(DependencyNode(id=name, resource_type=ResourceType.TABLE, name=name))
        # a depends on b (e.g. a foreign key from a to b); b depends on c.
        self.graph.add_edge(
            Dependency(
                source_id="a", target_id="b", relationship="fk", source=DependencySource.DATABASE_METADATA
            )
        )
        self.graph.add_edge(
            Dependency(
                source_id="b", target_id="c", relationship="fk", source=DependencySource.DATABASE_METADATA
            )
        )

    def test_downstream_of_c_includes_a_and_b(self):
        downstream_ids = {node.id for node, _, _ in self.graph.downstream_of("c")}
        self.assertEqual(downstream_ids, {"a", "b"})

    def test_upstream_of_a_includes_b_and_c(self):
        upstream_ids = {node.id for node, _, _ in self.graph.upstream_of("a")}
        self.assertEqual(upstream_ids, {"b", "c"})

    def test_downstream_of_a_is_empty(self):
        self.assertEqual(self.graph.downstream_of("a"), [])

    def test_add_edge_requires_both_nodes_to_exist(self):
        with self.assertRaises(KeyError):
            self.graph.add_edge(
                Dependency(
                    source_id="a", target_id="missing", relationship="fk", source=DependencySource.MANUAL
                )
            )

    def test_max_hops_limits_traversal(self):
        downstream_ids = {node.id for node, _, _ in self.graph.downstream_of("c", max_hops=1)}
        self.assertEqual(downstream_ids, {"b"})


class TestAcmeFinancialGraph(unittest.TestCase):
    """Builds the real demo graph -- exercises graph_builder + connectors.postgres
    metadata models together (see change_risk_engine.demo.build for the CLI/API
    equivalent)."""

    @classmethod
    def setUpClass(cls):
        builder = DependencyGraphBuilder()
        for name, metadata in build_demo_database_metadata().items():
            builder.add_database_metadata(name, metadata)
        builder.add_app_config(load_demo_dependency_config())
        cls.graph = builder.build()

    def test_foreign_key_edge_present(self):
        # customer_login has a real FK to customer -- it should depend on it.
        login_id = table_node_id("customer_db", "public", "customer_login")
        customer_id = table_node_id("customer_db", "public", "customer")
        downstream_of_customer = {node.id for node, _, _ in self.graph.downstream_of(customer_id)}
        self.assertIn(login_id, downstream_of_customer)

    def test_customer_table_reaches_critical_services(self):
        customer_id = table_node_id("customer_db", "public", "customer")
        blast_radius = BlastRadiusAnalyzer(self.graph).analyze([customer_id])
        self.assertIn("customer-portal", blast_radius.critical_dependencies)
        self.assertIn("payment-service", blast_radius.critical_dependencies)

    def test_isolated_table_has_narrow_blast_radius(self):
        # payment_method is only read/written by loan-service in the fixture.
        payment_method_id = table_node_id("payment_db", "public", "payment_method")
        blast_radius = BlastRadiusAnalyzer(self.graph).analyze([payment_method_id])
        self.assertLessEqual(len(blast_radius.downstream_impact), 2)
        self.assertNotIn("customer-portal", blast_radius.affected_services)


if __name__ == "__main__":
    unittest.main()
