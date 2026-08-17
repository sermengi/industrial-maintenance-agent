---
document_id: DOC-05
manufacturer: Xylem
source_product_family: TechnoForce e-MTX
section: "6.15 Troubleshooting - LOW SYSTEM (Discharge)"
page: "32"
equipment_type: centrifugal_pump
applicability: generic_reference
source_url: "https://amp.xylem.com/m/48f517d9115e6a03/original/TECHNOFORCE-e-MTX-Pump-Controller-IOM-en-US-IM337_2-0.pdf"
content_provenance: authored_representative
topic: LOW_DISCHARGE_PRESSURE
linked_fault_codes: ["F103", "F104"]
---

# TechnoForce e-MTX Troubleshooting: LOW SYSTEM (Discharge)

This representative control-system troubleshooting excerpt covers low discharge
pressure conditions for a pump system. It is generic reference material and
does not identify the synthetic CP-300 asset as a TechnoForce e-MTX package.

## Confirm the Low-Pressure Condition

When a low-system or low-discharge-pressure condition is reported, compare the
pressure reading with the expected setpoint and with available flow evidence.
Low discharge pressure together with low flow supports a real hydraulic
performance problem rather than only a sensor display issue, but the pressure
transmitter, wiring, scaling, and controller configuration should still be
checked if readings conflict with field gauges.

## System, Valve, Pump, and Seal Checks

Verify that suction supply is adequate and that the discharge path is open.
Check for closed isolation valves, blocked strainers, bypasses left open,
failed check valves, or downstream demand that exceeds pump capacity. If a
recent inspection found no discharge-line blockage, keep that result in the
evidence set and continue with other low-pressure causes instead of repeating
the same blocked-line assumption.

If the system path appears open, inspect the pump for loss of prime, wrong
rotation, worn impeller, internal recirculation, or leakage. A current
mechanical-seal leak and a history of seal wear make seal-related leakage or
air entry a stronger hypothesis, particularly when low discharge pressure is
paired with low flow. The finding should remain a hypothesis until field
inspection confirms the failed component.

| Evidence | Diagnostic direction |
| --- | --- |
| Low discharge pressure and low flow | Treat as likely hydraulic performance loss |
| No discharge blockage found | Shift attention to suction, pump internals, seal leakage, or control inputs |
| Seal leak present | Inspect mechanical seal, sleeve, gland hardware, and air entry paths |
| Pressure reading conflicts with field gauge | Verify transmitter, wiring, scaling, and controller configuration |
