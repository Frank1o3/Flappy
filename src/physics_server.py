"""
physics_server.py: The authoritative server-side world simulation.
"""

import random
from typing import Dict, List
from dataclasses import dataclass, field

from .constants import (
    PIPE_SPEED_PPS, PIPE_SPAWN_INTERVAL_TICKS,
    SCREEN_WIDTH, SCREEN_HEIGHT, PIPE_WIDTH, PIPE_GAP, BIRD_X
)
from .data_models import Player, Pipe
from .physics_core import PhysicsCore

@dataclass
class ServerEngine(PhysicsCore):
    """
    The authoritative engine managing the entire game state.
    Inherits core physics and collision from PhysicsCore.
    """
    tick_count: int = 0
    pipes: List[Pipe] = field(default_factory=list)
    pipe_spawn_counter: int = 0
    
    def _spawn_pipe(self):
        """Generates a new pipe off-screen to the right."""
        gap_y = random.randint(PIPE_GAP // 2, SCREEN_HEIGHT - PIPE_GAP // 2)
        self.pipes.append(Pipe(x=float(SCREEN_WIDTH), gap_y=float(gap_y)))
        self.pipe_spawn_counter = 0

    def step(self, players: Dict[str, Player], flap_inputs: Dict[str, bool]):
        """
        The main authoritative simulation step.
        Mutates player and pipe states.
        """
        self.tick_count += 1
        
        # 1. Spawn and Move Pipes
        self.pipe_spawn_counter += 1
        if self.pipe_spawn_counter >= PIPE_SPAWN_INTERVAL_TICKS:
            self._spawn_pipe()

        pipe_delta_x = PIPE_SPEED_PPS * self.DT 
        
        for pipe in self.pipes:
            pipe.x -= pipe_delta_x
            pipe.x = round(pipe.x, 4)
            
        self.pipes = [p for p in self.pipes if p.x > -PIPE_WIDTH]

        # 2. Update Players (apply inputs and physics)
        for username, player in players.items():
            if not player.alive:
                if flap_inputs.get(username, False):
                    self.respawn(player)
                continue

            # Apply flap impulse
            if flap_inputs.get(username, False):
                player.velocity = self.flap()

            # Apply gravity and movement
            player.y, player.velocity = self.apply_gravity_and_movement(
                player.y, player.velocity)
                
            # Check for collisions
            if self.check_collision(player.y, self.pipes):
                player.alive = False
            
            # 3. Score Update
            for pipe in self.pipes:
                if pipe.x < BIRD_X and pipe.x + pipe_delta_x >= BIRD_X:
                    player.score += 1