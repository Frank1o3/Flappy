"""
data_models.py: Data structures for the game state.
"""

from dataclasses import dataclass, field
from typing import Optional

from .constants import RESPAWN_Y

@dataclass
class Player:
    """The authoritative player state used by the server."""
    id: str
    y: float = RESPAWN_Y
    velocity: float = 0.0
    alive: bool = True
    score: int = 0
    
    # Network fields for server
    addr: Optional[tuple] = None           # (IP, Port)
    pending_flap: bool = False             # Did the player input a flap this tick?
    last_seq_received: int = 0             # The last input sequence number acknowledged

    def to_client_state(self):
        """Prepares a minimal state dictionary for network serialization."""
        return {
            "y": round(self.y, 2),
            "v": round(self.velocity, 2),
            "alive": self.alive,
            "score": self.score,
            "last_seq": self.last_seq_received
        }

@dataclass
class Pipe:
    """The authoritative pipe state."""
    x: float
    gap_y: float

@dataclass
class ClientPlayerState:
    """Client-side player state for prediction."""
    username: str
    y: float = RESPAWN_Y
    v: float = 0.0
    alive: bool = True
    score: int = 0

@dataclass
class ClientPipeState:
    """Client-side pipe state for prediction."""
    x: float
    gap_y: float