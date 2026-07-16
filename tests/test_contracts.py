from __future__ import annotations

import json
import unittest

from cgoprof.contracts import (
    APIContract,
    APIIdentity,
    APIKind,
    ArgumentCondition,
    BuildScope,
    Callback,
    CFunctionSignature,
    CTypeIdentity,
    ConditionOperator,
    ConditionResult,
    ConditionalClause,
    ContractAssignment,
    ContractAttribute,
    ContractCatalog,
    ContractFact,
    ContractStore,
    ContractTarget,
    ContractTargetKind,
    Encoding,
    Escape,
    Evidence,
    EvidenceKind,
    FactStatus,
    Lifetime,
    MemoryAccess,
    Mutability,
    Ownership,
    ParameterContract,
    ProviderIdentity,
    ProviderKind,
    Representation,
    RepresentationKind,
    ResultContract,
    TriState,
    ValueContract,
    dumps_catalog,
    evaluate_conditions,
    loads_catalog,
    merge_facts,
)


DECLARED = Evidence(
    kind=EvidenceKind.API_DOCUMENTATION,
    source="fixture API documentation",
    location="fixture.h:1",
)
PROVEN = Evidence(
    kind=EvidenceKind.C_BODY_ANALYSIS,
    source="fixture static analysis",
    location="fixture.c:1",
)
OBSERVED = Evidence(
    kind=EvidenceKind.DYNAMIC_OBSERVATION,
    source="fixture profile",
)
TEST_PROVIDER = ProviderIdentity(
    ProviderKind.SOURCE_BUNDLE,
    "example.com/fixture",
    "fixture",
)
TEST_API_ID = APIIdentity(
    provider=TEST_PROVIDER,
    symbol="fixture",
    kind=APIKind.FUNCTION,
    signature=CFunctionSignature(
        result=CTypeIdentity("c:int"),
        parameters=(CTypeIdentity("c:int"),),
    ),
).api_id


class ContractModelTests(unittest.TestCase):
    def test_value_contract_defaults_are_explicitly_unknown(self) -> None:
        contract = ValueContract()
        self.assertEqual(contract.memory_access.value, MemoryAccess.UNKNOWN)
        self.assertEqual(contract.ownership.value, Ownership.UNKNOWN)
        self.assertEqual(contract.lifetime.value, Lifetime.UNKNOWN)
        self.assertEqual(contract.escape.value, Escape.UNKNOWN)
        self.assertEqual(contract.mutability.value, Mutability.UNKNOWN)
        self.assertEqual(contract.representation.value, Representation.unknown())
        self.assertEqual(contract.memory_access.status, FactStatus.UNKNOWN)

    def test_non_unknown_status_requires_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "require evidence"):
            ContractFact(MemoryAccess.READ, status=FactStatus.PROVEN)

    def test_unknown_status_cannot_hide_a_concrete_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "must use the attribute's unknown value"):
            ContractFact(MemoryAccess.READ)

    def test_value_contract_rejects_a_fact_from_the_wrong_domain(self) -> None:
        with self.assertRaisesRegex(TypeError, "memory_access facts require MemoryAccess"):
            ValueContract(
                memory_access=ContractFact(
                    Ownership.UNKNOWN,
                    status=FactStatus.UNKNOWN,
                )
            )

    def test_contract_rejects_duplicate_parameter_indices(self) -> None:
        with self.assertRaisesRegex(ValueError, "parameter indices must be unique"):
            APIContract(
                api_id=TEST_API_ID,
                c_symbol="duplicate",
                parameters=(
                    ParameterContract(0, "left", "void*"),
                    ParameterContract(0, "right", "void*"),
                ),
            )

    def test_conditional_assignment_must_reference_existing_parameter(self) -> None:
        clause = ConditionalClause(
            conditions=(ArgumentCondition(0, ConditionOperator.EQ, 1),),
            assignments=(
                ContractAssignment(
                    target=ContractTarget(ContractTargetKind.PARAMETER, 2),
                    attribute=ContractAttribute.OWNERSHIP,
                    fact=ContractFact(
                        Ownership.BORROWED,
                        status=FactStatus.DECLARED,
                        evidence=(DECLARED,),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "unknown parameter 2"):
            APIContract(
                api_id=TEST_API_ID,
                c_symbol="conditional",
                parameters=(ParameterContract(0, "flag", "int"),),
                clauses=(clause,),
            )


class ConditionTests(unittest.TestCase):
    def test_conditions_use_three_valued_evaluation(self) -> None:
        conditions = (
            ArgumentCondition(0, ConditionOperator.EQ, "SQLITE_TRANSIENT"),
            ArgumentCondition(1, ConditionOperator.BIT_SET, 0b0100),
        )
        self.assertEqual(
            evaluate_conditions(conditions, {0: "SQLITE_TRANSIENT", 1: 0b1100}),
            ConditionResult.MATCH,
        )
        self.assertEqual(
            evaluate_conditions(conditions, {0: "SQLITE_STATIC", 1: 0b1100}),
            ConditionResult.NO_MATCH,
        )
        self.assertEqual(
            evaluate_conditions(conditions, {0: "SQLITE_TRANSIENT"}),
            ConditionResult.UNKNOWN,
        )

    def test_null_condition_rejects_an_extra_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "do not accept a value"):
            ArgumentCondition(0, ConditionOperator.IS_NULL, 0)


class ContractCodecTests(unittest.TestCase):
    def test_catalog_round_trip_preserves_typed_contract(self) -> None:
        contract = _sample_contract()
        catalog = ContractCatalog(
            contracts=(contract,),
            metadata=(("commit", "deadbeef"),),
        )
        serialized = dumps_catalog(catalog)
        restored = loads_catalog(serialized)
        self.assertEqual(restored, catalog)
        self.assertEqual(dumps_catalog(restored), serialized)
        parsed = json.loads(serialized)
        self.assertEqual(parsed["schema_version"], 2)
        self.assertEqual(parsed["contracts"][0]["api_id"], contract.api_id)

    def test_catalog_rejects_unsupported_schema(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported catalog schema version"):
            loads_catalog('{"schema_version": 99, "contracts": []}')

    def test_catalog_rejects_duplicate_api_ids(self) -> None:
        contract = _sample_contract()
        with self.assertRaisesRegex(ValueError, "duplicate api_id"):
            ContractCatalog(contracts=(contract, contract))

    def test_assignment_rejects_value_of_wrong_attribute_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "memory_access assignments require MemoryAccess"):
            ContractAssignment(
                target=ContractTarget(ContractTargetKind.PARAMETER, 0),
                attribute=ContractAttribute.MEMORY_ACCESS,
                fact=ContractFact(
                    Ownership.BORROWED,
                    status=FactStatus.DECLARED,
                    evidence=(DECLARED,),
                ),
            )

    def test_contract_store_uses_api_identity_and_symbol_index(self) -> None:
        contract = _sample_contract()
        store = ContractStore(ContractCatalog(contracts=(contract,)))
        self.assertIs(store.require(contract.api_id), contract)
        self.assertEqual(store.for_symbol("sqlite3_bind_text"), (contract,))
        self.assertEqual(store.for_symbol("missing"), ())
        with self.assertRaisesRegex(KeyError, "unknown cgo API contract"):
            store.require("missing")


class ContractLatticeTests(unittest.TestCase):
    def test_memory_read_and_write_join_without_losing_effects(self) -> None:
        outcome = merge_facts(
            ContractAttribute.MEMORY_ACCESS,
            ContractFact(MemoryAccess.READ, FactStatus.PROVEN, (PROVEN,)),
            ContractFact(MemoryAccess.WRITE, FactStatus.OBSERVED, (OBSERVED,)),
        )
        self.assertEqual(outcome.fact.value, MemoryAccess.READ_WRITE)
        self.assertEqual(outcome.fact.status, FactStatus.OBSERVED)
        self.assertEqual(outcome.diagnostics, ())

    def test_unknown_fact_is_missing_information_not_a_safe_fact(self) -> None:
        known = ContractFact(Escape.MAY_ESCAPE, FactStatus.PROVEN, (PROVEN,))
        outcome = merge_facts(
            ContractAttribute.ESCAPE,
            ContractFact(Escape.UNKNOWN),
            known,
        )
        self.assertEqual(outcome.fact, known)

    def test_positive_escape_dominates_noescape_and_records_conflict(self) -> None:
        outcome = merge_facts(
            ContractAttribute.ESCAPE,
            ContractFact(Escape.NO_ESCAPE, FactStatus.DECLARED, (DECLARED,)),
            ContractFact(Escape.ESCAPES, FactStatus.PROVEN, (PROVEN,)),
        )
        self.assertEqual(outcome.fact.value, Escape.ESCAPES)
        self.assertEqual(outcome.fact.status, FactStatus.CONFLICT)
        self.assertTrue(outcome.diagnostics)

    def test_observed_callback_dominates_no_callback_claim(self) -> None:
        outcome = merge_facts(
            ContractAttribute.CALLBACK,
            ContractFact(Callback.NO_CALLBACK, FactStatus.DECLARED, (DECLARED,)),
            ContractFact(Callback.OBSERVED_CALLBACK, FactStatus.OBSERVED, (OBSERVED,)),
        )
        self.assertEqual(outcome.fact.value, Callback.OBSERVED_CALLBACK)
        self.assertEqual(outcome.fact.status, FactStatus.CONFLICT)

    def test_incompatible_ownership_becomes_unknown_conflict(self) -> None:
        outcome = merge_facts(
            ContractAttribute.OWNERSHIP,
            ContractFact(Ownership.BORROWED, FactStatus.DECLARED, (DECLARED,)),
            ContractFact(Ownership.TRANSFERRED_TO_CALLEE, FactStatus.PROVEN, (PROVEN,)),
        )
        self.assertEqual(outcome.fact.value, Ownership.UNKNOWN)
        self.assertEqual(outcome.fact.status, FactStatus.CONFLICT)


def _sample_contract() -> APIContract:
    input_contract = ValueContract(
        memory_access=ContractFact(
            MemoryAccess.READ,
            status=FactStatus.DECLARED,
            evidence=(DECLARED,),
        ),
        ownership=ContractFact(
            Ownership.BORROWED,
            status=FactStatus.DECLARED,
            evidence=(DECLARED,),
        ),
        representation=ContractFact(
            Representation(
                kind=RepresentationKind.C_STRING,
                encoding=Encoding.UTF8,
                nul_terminated=TriState.CONDITIONAL,
                length_argument=3,
            ),
            status=FactStatus.DECLARED,
            evidence=(DECLARED,),
        ),
    )
    result_contract = ResultContract(
        c_type="int",
        contract=ValueContract(
            representation=ContractFact(
                Representation(kind=RepresentationKind.SCALAR),
                status=FactStatus.PROVEN,
                evidence=(PROVEN,),
            )
        ),
    )
    transient_clause = ConditionalClause(
        conditions=(ArgumentCondition(4, ConditionOperator.EQ, "SQLITE_TRANSIENT"),),
        assignments=(
            ContractAssignment(
                target=ContractTarget(ContractTargetKind.PARAMETER, 2),
                attribute=ContractAttribute.OWNERSHIP,
                fact=ContractFact(
                    Ownership.COPIED_BY_CALLEE,
                    status=FactStatus.DECLARED,
                    evidence=(DECLARED,),
                ),
            ),
            ContractAssignment(
                target=ContractTarget(ContractTargetKind.PARAMETER, 2),
                attribute=ContractAttribute.LIFETIME,
                fact=ContractFact(
                    Lifetime.CALL_SCOPED,
                    status=FactStatus.DECLARED,
                    evidence=(DECLARED,),
                ),
            ),
        ),
    )
    return APIContract(
        api_id=APIIdentity(
            provider=ProviderIdentity(
                ProviderKind.PKG_CONFIG,
                "sqlite.org",
                "sqlite3",
            ),
            symbol="sqlite3_bind_text",
            signature=CFunctionSignature(
                result=CTypeIdentity("c:int"),
                parameters=(
                    CTypeIdentity("c:sqlite3_stmt*"),
                    CTypeIdentity("c:int"),
                    CTypeIdentity("c:const char*"),
                    CTypeIdentity("c:int"),
                    CTypeIdentity("c:sqlite3_destructor_type"),
                ),
            ),
        ).api_id,
        c_symbol="sqlite3_bind_text",
        scope=BuildScope(
            go_package="example/sqlite",
            goos="linux",
            goarch="amd64",
            build_tags=("sqlite", "cgo"),
            c_macros_fingerprint="0123456789abcdef",
            library_version="3.x",
        ),
        parameters=(
            ParameterContract(0, "statement", "sqlite3_stmt*"),
            ParameterContract(1, "index", "int"),
            ParameterContract(2, "text", "const char*", input_contract),
            ParameterContract(3, "length", "int"),
            ParameterContract(4, "destructor", "sqlite3_destructor_type"),
        ),
        result=result_contract,
        callback=ContractFact(
            Callback.NO_CALLBACK,
            status=FactStatus.DECLARED,
            evidence=(DECLARED,),
        ),
        clauses=(transient_clause,),
        metadata=(("source", "fixture"),),
    )


if __name__ == "__main__":
    unittest.main()
