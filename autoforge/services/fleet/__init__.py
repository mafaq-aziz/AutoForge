"""Fleet service layer: the DATA step of the core loop.

Consumes factory ``FinishedVehicle`` records, operates them day by day against a
scenario on the shared engine, samples battery telemetry, and derives
maintenance events from battery faults and SOH.
"""
