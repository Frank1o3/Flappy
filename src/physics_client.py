"""
physics_client.py: The client-side prediction and reconciliation engine.
"""

from typing import List
from dataclasses import dataclass

from constants import PIPE_SPEED_PPS
from data_models import ClientPlayerState, ClientPipeState
from physics_core import PhysicsCore

@dataclass
class ClientEngine(PhysicsCore):
    """
    Client-side physics engine for local player prediction.
    Inherits core physics and collision from PhysicsCore.
    """
    
    def step_player(self, player: ClientPlayerState, flap: bool, pipes: List[ClientPipeState]):
        """
        Deterministic single-tick update for the local player's prediction.
        Mutates the player state.
        """
        if not player.alive:
            return

        # 1. Apply flap input
        if flap:
            player.v = self.flap()

        # 2. Apply gravity and movement
        player.y, player.v = self.apply_gravity_and_movement(player.y, player.v)

        # 3. Collision check
        # We pass the client's y and the local pipes to the core logic.
        if self.check_collision(player.y, pipes):
            player.alive = False
            
        # If collision check killed the player, clamp position
        if not player.alive:
            # SCREEN_HEIGHT is available via inheritance from PhysicsCore's constants import
            if hasattr(self, 'SCREEN_HEIGHT'):
                player.y = max(min(player.y, self.SCREEN_HEIGHT), 0)
            player.v = 0.0

    def step_pipes(self, pipes: List[ClientPipeState]):
        """Moves pipes for client-side prediction."""
        pipe_delta_x = PIPE_SPEED_PPS * self.DT 
        
        for pipe in pipes:
            pipe.x -= pipe_delta_x
            pipe.x = round(pipe.x, 4)