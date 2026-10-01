"""
Satellite Management & Arbitration Bounded Context (Domain).
Exports SatellitePipelineSession, MultiRoomArbiter, ArbiterCandidate, and protocol messages.
"""
from server.domain.satellite.protocol import InitMessage, ControlMessage, ConfigUpdateMessage, parse_incoming_message
from server.domain.satellite.arbiter import MultiRoomArbiter, ArbiterCandidate
from server.domain.satellite.pipeline import SatellitePipelineSession

__all__ = [
    "InitMessage",
    "ControlMessage",
    "ConfigUpdateMessage",
    "parse_incoming_message",
    "MultiRoomArbiter",
    "ArbiterCandidate",
    "SatellitePipelineSession",
]
