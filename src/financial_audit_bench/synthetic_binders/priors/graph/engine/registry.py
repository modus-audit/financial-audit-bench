"""Rule registry and executable graph runtime for binder worlds."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from typing import Any, Protocol

World = dict[str, Any]


def _caller_family() -> str:
    """The domain package registering the current rule, as its short name."""
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module != __name__:
            parts = module.split(".")
            if "domains" in parts:
                domain_index = parts.index("domains")
                if len(parts) > domain_index + 2:
                    return parts[domain_index + 1]
            # graph rule modules live at domains/<domain>/rules.py, so a
            # module named "rules" is attributed to its domain package.
            if parts[-1] == "rules" and len(parts) > 1:
                return parts[-2]
            return parts[-1]
        frame = frame.f_back
    return ""


@dataclass(frozen=True)
class RunResult:
    """Output of one graph execution: node populations and checks."""

    world: World
    checks: dict[str, Any]


class ValueSource(Protocol):
    """Provides a value for each sampled node."""

    def sample(self, node_id: str, world: World) -> Any: ...

    def has(self, node_id: str) -> bool: ...


@dataclass(frozen=True)
class Rule:
    """One generation step: inputs -> outputs under a named mechanism."""

    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    fn: Callable[..., Any]
    gate: str | None = None  # Feature flag controlling rule execution.
    family: str = ""  # Rule group used for business-type composition.
    contribution_priority: int = 0  # Merge order for shared outputs.

    def __post_init__(self) -> None:
        if not self.outputs:
            raise ValueError(f"rule {self.name} must declare outputs")


@dataclass(frozen=True)
class Check:
    """An accounting invariant over materialized nodes (never a graph edge)."""

    name: str
    inputs: tuple[str, ...]
    fn: Callable[..., Any]


@dataclass
class RuleRegistry:
    """Declarative registry of nodes, rules, invariants, and finalizers."""

    nodes: dict[str, bool] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    finalizers: dict[str, list[Callable[..., None]]] = field(default_factory=dict)

    def node(
        self,
        node_id: str,
        *,
        singleton: bool = False,
    ) -> None:
        if node_id in self.nodes:
            raise ValueError(f"duplicate node: {node_id}")
        self.nodes[node_id] = singleton

    def rule(
        self,
        name: str,
        *,
        inputs: Iterable[str] = (),
        outputs: Iterable[str] | str,
        gate: str | None = None,
        contribution_priority: int = 0,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register ``fn(*input_values)`` producing the output populations."""
        output_ids = (outputs,) if isinstance(outputs, str) else tuple(outputs)

        family = _caller_family()

        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            if any(rule.name == name for rule in self.rules):
                raise ValueError(f"duplicate rule: {name}")
            self.rules.append(
                Rule(
                    name=name,
                    inputs=tuple(inputs),
                    outputs=output_ids,
                    fn=fn,
                    gate=gate,
                    family=family,
                    contribution_priority=contribution_priority,
                )
            )
            return fn

        return register

    def sample(
        self,
        node_id: str,
        *,
        inputs: Iterable[str] = (),
        gate: str | None = None,
        rule_name: str | None = None,
        contribution_priority: int = 0,
    ) -> None:
        """Register a sampled node whose value comes from the ValueSource."""
        name = rule_name or f"sample_{node_id}"
        self.rule(
            name,
            inputs=inputs,
            outputs=node_id,
            gate=gate,
            contribution_priority=contribution_priority,
        )(_SourceSample(node_id))

    def check(
        self,
        name: str,
        *,
        inputs: Iterable[str],
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register an invariant run after its inputs are materialized."""

        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.checks.append(
                Check(
                    name=name,
                    inputs=tuple(inputs),
                    fn=fn,
                )
            )
            return fn

        return register

    def finalize(
        self, node_id: str
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        """Register a normalization pass run once a node is fully built."""

        def register(fn: Callable[..., None]) -> Callable[..., None]:
            self.finalizers.setdefault(node_id, []).append(fn)
            return fn

        return register

    # -- composition --------------------------------------------------------

    def for_families(self, families: frozenset[str]) -> "RuleRegistry":
        """Compose a registry containing only the given rule families."""
        rules = [rule for rule in self.rules if rule.family in families]
        produced = {node_id for rule in rules for node_id in rule.outputs}
        # Retain declaration-only placeholders because projection reads some optional
        # populations directly. They have no producers or graph edges and graph_export
        # excludes them from the released topology.
        composed = RuleRegistry(
            nodes=dict(self.nodes),
            rules=rules,
            checks=[check for check in self.checks if set(check.inputs) <= produced],
            finalizers={
                node_id: fns
                for node_id, fns in self.finalizers.items()
                if node_id in produced
            },
        )
        composed.validate()
        return composed

    # -- execution ----------------------------------------------------------

    def unproduced_inputs(self) -> set[str]:
        """Declared nodes referenced only as inputs, produced by no rule."""
        produced = {node_id for rule in self.rules for node_id in rule.outputs}
        return set(self.nodes) - produced

    def validate(self) -> None:
        """Fail fast on unknown references and duplicate singleton producers."""
        produced: dict[str, list[str]] = {}
        for rule in self.rules:
            for node_id in (*rule.inputs, *rule.outputs):
                if node_id not in self.nodes:
                    raise ValueError(
                        f"rule {rule.name} references unknown node {node_id}"
                    )
            for node_id in rule.outputs:
                produced.setdefault(node_id, []).append(rule.name)
        for check in self.checks:
            for node_id in check.inputs:
                if node_id not in self.nodes:
                    raise ValueError(
                        f"check {check.name} references unknown node {node_id}"
                    )
        for node_id, rule_names in produced.items():
            if self.nodes[node_id] and len(rule_names) > 1:
                raise ValueError(
                    f"singleton node {node_id} has multiple producers: {rule_names}"
                )

    def execution_order(self) -> list[Rule]:
        """Topologically order rules by their node dependencies."""
        producers: dict[str, list[Rule]] = {}
        for rule in self.rules:
            for node_id in rule.outputs:
                producers.setdefault(node_id, []).append(rule)
        sorter: TopologicalSorter[str] = TopologicalSorter()
        rule_by_name = {rule.name: rule for rule in self.rules}
        for rule in self.rules:
            dependencies = {
                producer.name
                for node_id in rule.inputs
                for producer in producers.get(node_id, [])
                if producer.name != rule.name
            }
            if rule.gate:
                dependencies.update(
                    producer.name
                    for producer in producers.get("company_feature_profile", [])
                )
            sorter.add(rule.name, *dependencies)
        ordered = list(sorter.static_order())
        # Order among rules with identical constraints is (contribution priority,
        # registration order), so contributions to shared nodes are deterministic and
        # independent of module import order.
        registration_rank = {
            rule.name: (rule.contribution_priority, index)
            for index, rule in enumerate(self.rules)
        }
        return sorted(
            (rule_by_name[name] for name in ordered),
            key=lambda rule: (
                _depth(rule, producers, {}),
                registration_rank[rule.name],
            ),
        )

    def run(self, source: ValueSource, context: World | None = None) -> "RunResult":
        """Materialize every node and run every invariant."""
        self.validate()
        world: World = dict(context or {})
        # A node an excluded family would have produced is seeded empty, so a surviving
        # rule that reads it contributes nothing (composition's "gated off" equivalent).
        # Empty for the full registry.
        for node_id in self.unproduced_inputs():
            world.setdefault(node_id, None if self.nodes[node_id] else [])
        # Populations with several producing rules assemble by (contribution priority,
        # registration order), independent of execution order, so merged node contents
        # are deterministic.
        registration_rank = {
            rule.name: (rule.contribution_priority, index)
            for index, rule in enumerate(self.rules)
        }
        producer_count: dict[str, int] = {}
        contributions: dict[str, dict[int, Any]] = {}
        for rule in self.rules:
            for node_id in rule.outputs:
                producer_count[node_id] = producer_count.get(node_id, 0) + 1
                contributions.setdefault(node_id, {})

        ordered_rules = self.execution_order()
        for rule in ordered_rules:
            gated_off = rule.gate is not None and not world.get(
                "company_feature_profile", {}
            ).get(rule.gate, False)
            if gated_off:
                values: tuple[Any, ...] = tuple(
                    None if self.nodes[node_id] else [] for node_id in rule.outputs
                )
            else:
                arguments = [world[node_id] for node_id in rule.inputs]
                result = _execute_rule(rule, source, world, arguments)
                values = result if len(rule.outputs) > 1 else (result,)
                if len(values) != len(rule.outputs):
                    raise ValueError(f"rule {rule.name} returned wrong output arity")
            for node_id, value in zip(rule.outputs, values, strict=True):
                node_contributions = contributions[node_id]
                node_contributions[registration_rank[rule.name]] = value
                if len(node_contributions) < producer_count[node_id]:
                    continue
                spec = self.nodes[node_id]
                if spec:
                    world[node_id] = next(iter(node_contributions.values()))
                else:
                    world[node_id] = [
                        row
                        for _, rows in sorted(node_contributions.items())
                        for row in rows or []
                    ]
                for finalizer in self.finalizers.get(node_id, []):
                    finalizer(world)

        self._verify_node_shapes(world)
        # Run every check and collect structured results, so a failing seed
        # reports which identities failed (and where), not just the first.
        check_results = []
        failures = []
        for check in self.checks:
            try:
                outcome = check.fn(*(world[node_id] for node_id in check.inputs))
                result = {"check": check.name, "status": "passed"}
                # Error-injection-aware checks return the documented planted exceptions
                # they validated (and would fail on anything undocumented), so the run
                # summary identifies each cross-document break as the intended one.
                if outcome:
                    result["intended_exceptions"] = outcome
                check_results.append(result)
            except Exception as error:
                check_results.append(
                    {"check": check.name, "status": "failed", "error": str(error)}
                )
                failures.append(f"{check.name}: {error}")
        if failures:
            raise ValueError(
                "world identity checks failed:\n  " + "\n  ".join(failures)
            )
        return RunResult(
            world={node_id: world[node_id] for node_id in self.nodes},
            checks={"status": "passed", "results": check_results},
        )

    def _verify_node_shapes(self, world: World) -> None:
        """Require every row in a population to use one consistent shape."""
        for node_id, spec in self.nodes.items():
            value = world.get(node_id)
            rows = [value] if spec else value or []
            expected: set[str] | None = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                actual = set(row)
                if expected is None:
                    expected = actual
                elif actual != expected:
                    raise ValueError(
                        f"node {node_id} rows have inconsistent shapes: "
                        f"extra={sorted(actual - expected)} "
                        f"missing={sorted(expected - actual)}"
                    )
        return None


def _execute_rule(
    rule: Rule,
    source: ValueSource,
    world: World,
    arguments: list[Any],
) -> Any:
    if isinstance(rule.fn, _SourceSample):
        return rule.fn(source, world)
    return rule.fn(*arguments)


class _SourceSample:
    """Callable marker delegating a sampled node to the ValueSource."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id

    def __call__(self, source: ValueSource, world: World) -> Any:
        return source.sample(self.node_id, world)


def _depth(
    rule: Rule,
    producers: dict[str, list[Rule]],
    memo: dict[str, int],
) -> int:
    """Longest input chain below the rule; used for stable ordering."""
    if rule.name in memo:
        return memo[rule.name]
    memo[rule.name] = 0  # Break recursion; graphlib validates cycles separately.
    depth = 0
    dependency_nodes = set(rule.inputs)
    if rule.gate:
        dependency_nodes.add("company_feature_profile")
    for node_id in dependency_nodes:
        for producer in producers.get(node_id, []):
            if producer.name != rule.name:
                depth = max(depth, _depth(producer, producers, memo) + 1)
    memo[rule.name] = depth
    return depth
