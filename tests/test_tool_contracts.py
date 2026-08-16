import inspect
import types
from collections.abc import Callable
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from sqlalchemy.ext.asyncio import AsyncSession

from maintenance_agent.db.repositories.records import AssetRecord
from maintenance_agent.tools import (
    get_asset_status,
    get_maintenance_history,
    get_plant_policy,
    resolve_asset,
)
from maintenance_agent.tools.get_asset_status import (
    ClassifiedReading,
    GetAssetStatusResult,
)
from maintenance_agent.tools.get_maintenance_history import (
    FaultRecurrence,
    GetMaintenanceHistoryResult,
)
from maintenance_agent.tools.get_plant_policy import GetPlantPolicyResult
from maintenance_agent.tools.resolve_asset import ResolveAssetResult

TOOL_MODULES = [
    resolve_asset,
    get_asset_status,
    get_maintenance_history,
    get_plant_policy,
]

TOOL_FUNCTIONS: list[tuple[Callable[..., Any], list[tuple[str, Any]], type[BaseModel]]] = [
    (
        resolve_asset.resolve_asset,
        [("identifier", str), ("session", AsyncSession)],
        ResolveAssetResult,
    ),
    (
        get_asset_status.get_asset_status,
        [("asset", AssetRecord), ("session", AsyncSession)],
        GetAssetStatusResult,
    ),
    (
        get_maintenance_history.get_maintenance_history,
        [("asset", AssetRecord), ("session", AsyncSession)],
        GetMaintenanceHistoryResult,
    ),
    (
        get_plant_policy.get_plant_policy,
        [("policy_type", str), ("session", AsyncSession)],
        GetPlantPolicyResult,
    ),
]

RESULT_MODELS = [
    ResolveAssetResult,
    GetAssetStatusResult,
    GetMaintenanceHistoryResult,
    GetPlantPolicyResult,
]

TOOL_MODELS = [
    ResolveAssetResult,
    ClassifiedReading,
    GetAssetStatusResult,
    FaultRecurrence,
    GetMaintenanceHistoryResult,
    GetPlantPolicyResult,
]


def test_tool_result_models_follow_name_convention() -> None:
    assert [model.__name__ for model in RESULT_MODELS] == [
        "ResolveAssetResult",
        "GetAssetStatusResult",
        "GetMaintenanceHistoryResult",
        "GetPlantPolicyResult",
    ]
    assert ClassifiedReading.__name__ == "ClassifiedReading"
    assert FaultRecurrence.__name__ == "FaultRecurrence"


def test_tools_do_not_define_input_or_request_models() -> None:
    forbidden_suffixes = ("Input", "Request")

    for module in TOOL_MODULES:
        model_names = [
            name
            for name, value in vars(module).items()
            if inspect.isclass(value) and issubclass(value, BaseModel)
        ]

        assert all(not name.endswith(forbidden_suffixes) for name in model_names)


def test_tool_function_signatures_follow_contract() -> None:
    for tool_function, expected_parameters, expected_return in TOOL_FUNCTIONS:
        signature = inspect.signature(tool_function)

        assert inspect.iscoroutinefunction(tool_function)
        assert [
            (parameter.name, parameter.annotation) for parameter in signature.parameters.values()
        ] == expected_parameters
        assert signature.return_annotation is expected_return


def test_collection_fields_default_to_empty_lists() -> None:
    expected_collection_fields = {
        GetAssetStatusResult: [
            "classified_readings",
            "active_faults",
            "observations",
            "operating_limits",
        ],
        GetMaintenanceHistoryResult: [
            "maintenance_events",
            "fault_events",
            "work_orders",
            "recurrence",
        ],
        GetPlantPolicyResult: ["policies"],
    }

    for model, field_names in expected_collection_fields.items():
        for field_name in field_names:
            field = model.model_fields[field_name]

            assert _is_list_annotation(field.annotation)
            assert _field_default_is_empty_list(field)


def test_optional_scalar_fields_are_explicitly_nullable() -> None:
    expected_nullable_fields = {
        ResolveAssetResult: ["asset"],
        GetAssetStatusResult: ["telemetry"],
        ClassifiedReading: ["tier", "operating_limit_id", "rule_text"],
    }

    for model, field_names in expected_nullable_fields.items():
        for field_name in field_names:
            assert _annotation_allows_none(model.model_fields[field_name].annotation)


def test_tool_model_fields_do_not_use_float_annotations() -> None:
    for model in TOOL_MODELS:
        for field in model.model_fields.values():
            assert not _annotation_contains_float(field.annotation)


def _field_default_is_empty_list(field: FieldInfo) -> bool:
    if field.default == []:
        return True
    if field.default_factory is list:
        return True
    return False


def _is_list_annotation(annotation: Any) -> bool:
    return get_origin(annotation) is list


def _annotation_allows_none(annotation: Any) -> bool:
    return type(None) in get_args(annotation)


def _annotation_contains_float(annotation: Any) -> bool:
    if annotation is float:
        return True

    origin = get_origin(annotation)
    if origin is None:
        return False

    if origin in {types.UnionType, list, dict, tuple, set}:
        return any(_annotation_contains_float(argument) for argument in get_args(annotation))

    return False
