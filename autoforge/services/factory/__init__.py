"""Factory service layer: the production side of the core loop.

Runs orders through a configured line on the shared engine with seeded
defects, downtime, and material shortages, producing typed results with
finished vehicles for the fleet phase.
"""
