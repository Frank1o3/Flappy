"""
constants.py: Centralized configuration for game and network settings.
"""

# -------- Network & Server Config --------
GAME_PORT = 50007
DISCOVERY_PORT = 37020
BUFFER_SIZE = 65536
SERVER_DISCOVERY_TIMEOUT = 5.0  # seconds

# Time synchronization
SERVER_TICK_RATE = 30           # Authoritative server ticks per second
TICK_TIME = 1.0 / SERVER_TICK_RATE # Fixed time step (Delta Time)
CLIENT_TICK_RATE = SERVER_TICK_RATE # Client prediction rate (should match server)

# -------- Game World Config --------
SCREEN_WIDTH = 480
SCREEN_HEIGHT = 800
BIRD_X = 100                    # Fixed bird X position
RESPAWN_Y = SCREEN_HEIGHT // 2

# -------- Pipe Config --------
PIPE_WIDTH = 80
PIPE_GAP = 200
PIPE_SPEED_PPS = 250.0          # Horizontal speed (pixels/second)
PIPE_SPAWN_INTERVAL_TICKS = 90  # Spawn every 90 ticks (3.0 seconds)

# -------- Physics Config (Pixels / Second / Second) --------
# Time-based constants for deterministic physics
GRAVITY_ACCEL = 1800.0          # Vertical acceleration (pixels/s^2)
JUMP_IMPULSE = -600.0           # Instantaneous velocity change (pixels/s)
MAX_FALL_VELOCITY = 1000.0      # Clamping for stability (pixels/s)
BIRD_RADIUS = 20                # For collision detection