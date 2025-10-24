"""
physics_core.py: The shared, deterministic kinematic functions and collision logic.
"""

from typing import List, Union # <-- ADDED Union for type flexibility
from .constants import (
    GRAVITY_ACCEL, JUMP_IMPULSE, MAX_FALL_VELOCITY, TICK_TIME,
    SCREEN_HEIGHT, BIRD_X, PIPE_WIDTH, PIPE_GAP, BIRD_RADIUS, RESPAWN_Y
)
# Imported both Pipe models for the shared check_collision function
from .data_models import Player, Pipe, ClientPipeState 

class PhysicsCore:
    """
    Shared deterministic physics core used by both server and client engines.
    """
    
    DT = TICK_TIME 
    SCREEN_HEIGHT = SCREEN_HEIGHT # <-- EXPOSED as class attribute for easy access
    # Note: BIRD_RADIUS is also available via self.BIRD_RADIUS if needed
    
    def apply_gravity_and_movement(self, y: float, velocity: float) -> tuple[float, float]:
        """
        Calculates new velocity and position after one fixed timestep (DT).
        """
        velocity += GRAVITY_ACCEL * self.DT
        velocity = min(velocity, MAX_FALL_VELOCITY)
        y += velocity * self.DT
        
        y = round(y, 4)
        velocity = round(velocity, 4)
        
        return y, velocity

    def flap(self) -> float:
        """Returns the instantaneous velocity after a flap."""
        return JUMP_IMPULSE

    # FIXED: Accepts a list of either Pipe or ClientPipeState
    def check_collision(self, y: float, pipes: List[Union[Pipe, ClientPipeState]]) -> bool:
        """Checks for collisions with floor, ceiling, or pipes."""
        
        # 1. Floor/Ceiling Collision
        # Using the exposed class attribute for SCREEN_HEIGHT
        if y <= 0 or y >= self.SCREEN_HEIGHT: 
            return True

        # 2. Pipe Collision
        for pipe in pipes:
            if pipe.x - BIRD_RADIUS < BIRD_X < pipe.x + PIPE_WIDTH + BIRD_RADIUS:
                
                pipe_bottom_y = pipe.gap_y + PIPE_GAP // 2
                pipe_top_y = pipe.gap_y - PIPE_GAP // 2
                
                if not (pipe_top_y < y < pipe_bottom_y):
                    return True
                    
        return False
    
    def respawn(self, player: Player):
        player.y = RESPAWN_Y
        player.velocity = 0.0
        player.alive = True
        player.score = 0