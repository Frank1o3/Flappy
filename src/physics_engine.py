#!/usr/bin/env python3
"""
Flappy Bird Multiplayer Physics
Split into shared core, server engine, and client engine.
"""

import random
from typing import Optional, List
from dataclasses import dataclass, field

# ---------- Game Constants ----------
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 800
PIPE_WIDTH = 80
PIPE_GAP = 200
PIPE_SPEED = 4
PIPE_SPAWN_INTERVAL = 90  # ticks
BIRD_X = 100  # fixed bird x position

# ---------- Shared Core ----------
@dataclass
class Player:
    id: str
    y: float = SCREEN_HEIGHT // 2
    velocity: float = 0.0
    alive: bool = True
    score: int = 0
    addr: Optional[tuple] = None  # Network address for server communication
    pending_flap: bool = False  # For server to track input
    last_seq_received: int = 0  # For server to track input sequence


@dataclass
class Pipe:
    x: float
    gap_y: float


class PhysicsCore:
    """Shared deterministic physics used by client and server."""

    def __init__(self, gravity=0.45, flap_strength=-9, terminal_velocity=15):
        self.gravity = gravity
        self.flap_strength = flap_strength
        self.terminal_velocity = terminal_velocity

    def apply_gravity(self, velocity: float) -> float:
        velocity += self.gravity
        return min(velocity, self.terminal_velocity)

    def flap(self) -> float:
        return self.flap_strength

    def step_player(self, player: Player, flap: bool, pipes: list[Pipe]):
        """Update a single player's state for one tick."""
        if not player.alive:
            return

        if flap:
            player.velocity = self.flap()

        player.velocity = self.apply_gravity(player.velocity)
        player.y += player.velocity

        # Ground / ceiling collision
        if player.y <= 0 or player.y >= SCREEN_HEIGHT:
            player.alive = False

        # Pipe collisions
        for pipe in pipes:
            if pipe.x < BIRD_X < pipe.x + PIPE_WIDTH:
                if not (pipe.gap_y < player.y < pipe.gap_y + PIPE_GAP):
                    player.alive = False

        # Score (when passing a pipe)
        for pipe in pipes:
            if pipe.x + PIPE_WIDTH == BIRD_X:
                player.score += 1


# ---------- Server Engine ----------
@dataclass
class ServerEngine:
    core: PhysicsCore = field(default_factory=PhysicsCore)
    tick_count: int = 0
    pipes: list = field(default_factory=list)

    def spawn_pipe(self):
        gap_y = random.randint(100, SCREEN_HEIGHT - 100 - PIPE_GAP)
        self.pipes.append(Pipe(SCREEN_WIDTH, gap_y))

    def step_pipes(self):
        for pipe in self.pipes:
            pipe.x -= PIPE_SPEED
        self.pipes = [p for p in self.pipes if p.x + PIPE_WIDTH > 0]

        if self.tick_count % PIPE_SPAWN_INTERVAL == 0:
            self.spawn_pipe()

    def step(self, players: dict[str, Player], flaps: dict[str, bool]):
        """Server tick: updates all players and pipes."""
        self.tick_count += 1
        self.step_pipes()

        for pid, player in players.items():
            self.core.step_player(player, flap=flaps.get(pid, False), pipes=self.pipes)


# ----------------- Client Physics (shared deterministic) -----------------
@dataclass
class PlayerState:
    username: str
    y: float = SCREEN_HEIGHT // 2
    v: float = 0.0
    alive: bool = True
    score: int = 0

@dataclass
class PipeState:
    x: float
    gap_y: float

# ---------- Client Engine ----------
@dataclass
class ClientEngine:
    def __init__(self, gravity=0.45, flap_strength=-9.0, terminal_velocity=15.0):
        self.gravity = gravity
        self.flap_strength = flap_strength
        self.terminal_velocity = terminal_velocity

    def apply_gravity(self, velocity: float) -> float:
        velocity += self.gravity
        if velocity > self.terminal_velocity:
            velocity = self.terminal_velocity
        return velocity

    def flap(self) -> float:
        return self.flap_strength

    def step_player(self, player: PlayerState, flap: bool, pipes: List[PipeState]):
        """Deterministic single-tick update (mutates player)."""
        if not player.alive:
            return
        if flap:
            player.v = self.flap()
        player.v = self.apply_gravity(player.v)
        player.y += player.v

        # Floor/Ceiling
        if player.y <= 0 or player.y >= SCREEN_HEIGHT:
            player.alive = False

        # Pipe collision (bird X fixed)
        for pipe in pipes:
            if pipe.x < BIRD_X < pipe.x + PIPE_WIDTH:
                if not (pipe.gap_y < player.y < pipe.gap_y + PIPE_GAP):
                    player.alive = False
