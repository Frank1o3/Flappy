#!/usr/bin/env python3
"""
flappy_client.py

Client with prediction + reconciliation and pygame rendering.

Usage:
    python flappy_client.py
"""

import socket
import json
import threading
import time
import pygame
from dataclasses import dataclass
from typing import Dict, List
from physics_engine import SCREEN_WIDTH, SCREEN_HEIGHT, BIRD_X, PIPE_WIDTH, PIPE_GAP, ClientEngine, PlayerState, PipeState

# ----------------- Network Config -----------------
GAME_PORT = 50007
DISCOVERY_PORT = 37020
BUFFER_SIZE = 65536

SERVER_DISCOVERY_TIMEOUT = 5.0  # seconds
SERVER_TICK_RATE = 30.0  # authoritative server tick/s
CLIENT_TICK_RATE = SERVER_TICK_RATE  # run prediction at same tick rate
RENDER_FPS = 60

# ----------------- Network Client (discovery / login / state) -----------------


class NetworkClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

        # Game socket for login + input + receiving state
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Set a reasonable recv timeout
        self.sock.settimeout(2.0)

        # Discovery socket to listen to broadcast from server
        self.discovery_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind to discovery port to receive server broadcast packets
        try:
            self.discovery_sock.bind(("", DISCOVERY_PORT))
            self.discovery_sock.settimeout(SERVER_DISCOVERY_TIMEOUT)
        except Exception:
            # Some platforms need different binding; fallback to non-blocking
            self.discovery_sock.settimeout(1.0)

        self.server_addr = None  # (ip, port)
        self.logged_in = False

        # Latest authoritative state from server
        self.latest_state = {}
        self.state_lock = threading.Lock()

        # Thread control
        self.listening = False

    def discover_server(self, timeout=SERVER_DISCOVERY_TIMEOUT) -> bool:
        """Wait for the server's broadcast packet."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                data, addr = self.discovery_sock.recvfrom(BUFFER_SIZE)
                msg = json.loads(data.decode())
                if msg.get("type") == "discovery":
                    self.server_addr = (addr[0], msg.get("port", GAME_PORT))
                    print(f"[net] Discovered server at {self.server_addr}")
                    return True
            except socket.timeout:
                continue
            except Exception:
                continue
        return False

    def send_json(self, obj):
        if not self.server_addr:
            return
        try:
            self.sock.sendto(json.dumps(obj).encode(), self.server_addr)
        except Exception as e:
            print("[net] send error:", e)

    def login(self):
        if not self.server_addr:
            return False
        msg = {"type": "login", "username": self.username, "password": self.password}
        self.send_json(msg)
        return True

    def send_input(self, flap: bool, seq: int):
        msg = {"type": "input", "username": self.username, "flap": flap, "seq": seq}
        self.send_json(msg)

    def start_listen(self):
        self.listening = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop(self):
        self.listening = False

    def _listen_loop(self):
        while self.listening:
            try:
                data, addr = self.sock.recvfrom(BUFFER_SIZE)
                msg = json.loads(data.decode())
                self._handle_message(msg)
            except socket.timeout:
                continue
            except Exception:
                continue

    def _handle_message(self, msg):
        mtype = msg.get("type")
        if mtype == "login_ok":
            self.logged_in = True
            print(f"[net] Logged in as {self.username}")
        elif mtype == "login_fail":
            print("[net] Login failed:", msg.get("reason"))
            self.logged_in = False
        elif mtype == "state":
            # store the authoritative state
            with self.state_lock:
                self.latest_state = msg
        # else: ignore other messages for now

    def fetch_state(self):
        with self.state_lock:
            return self.latest_state.copy() if self.latest_state else None


# ----------------- Client Prediction & Reconciliation -----------------


@dataclass
class PendingInput:
    seq: int
    flap: bool


class ClientGame:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

        # Networking
        self.net = NetworkClient(username, password)

        # Physics
        self.core = ClientEngine()

        # Local scene (predicted)
        self.local_players: Dict[str, PlayerState] = {}
        self.pipes: List[PipeState] = []

        # Input buffer for local player (unacked)
        self.next_input_seq = 1
        self.pending_inputs: List[PendingInput] = []

        # Timing
        self.client_tick_dt = 1.0 / CLIENT_TICK_RATE
        self.last_tick_time = time.time()

        # Reconciliation threshold (pixels) - if authoritative differs by > threshold, correct
        self.reconcile_threshold = 1.5

        # Game control
        self.running = True

    # ----- Networking helpers -----
    def discover_and_login(self) -> bool:
        found = self.net.discover_server()
        if not found:
            print("[client] No server discovered on LAN.")
            return False
        self.net.login()
        self.net.start_listen()
        # Wait briefly for login_ok
        t0 = time.time()
        while time.time() - t0 < 2.0:
            if self.net.logged_in:
                return True
            time.sleep(0.05)
        # Might still become logged in later; continue anyway
        return self.net.logged_in

    # ----- Input handling -----
    def send_flap(self):
        # do not allow flaps while dead locally
        local = self.local_players.get(self.username)
        if local and not local.alive:
            # dead players should press R to respawn
            return

        seq = self.next_input_seq
        self.next_input_seq += 1
        # Store pending input locally
        self.pending_inputs.append(PendingInput(seq, True))
        # Send to server
        self.net.send_input(flap=True, seq=seq)
        # Immediately apply prediction locally
        self.apply_input_local(True)

    def apply_input_local(self, flap: bool):
        # Ensure local player exists
        if self.username not in self.local_players:
            self.local_players[self.username] = PlayerState(self.username)
        self.core.step_player(
            self.local_players[self.username], flap, self.pipes)

    def tick_local(self):
        """Called at client tick rate: apply gravity etc without new input (flap=False)."""
        if self.username not in self.local_players:
            self.local_players[self.username] = PlayerState(self.username)
        self.core.step_player(
            self.local_players[self.username], False, self.pipes)

    # ----- Reconciliation -----
    def reconcile_with_authoritative(self, auth_state: dict):
        """
        Improved reconciliation:
         - read server-provided last_seq for this player and purge confirmed inputs
         - replay only remaining pending inputs
        """
        if not auth_state:
            return

        players = auth_state.get("players", {})
        if self.username not in players:
            return

        auth_p = players[self.username]
        auth_y = float(auth_p.get("y", 0.0))
        auth_v = float(auth_p.get("v", 0.0))
        auth_alive = bool(auth_p.get("alive", True))
        auth_score = int(auth_p.get("score", 0))
        auth_last_seq = int(auth_p.get("last_seq", 0))

        # Update remote players and pipes from authoritative state
        new_local_players: Dict[str, PlayerState] = {}
        for uname, pdata in players.items():
            st = PlayerState(uname, float(pdata.get("y", SCREEN_HEIGHT//2)), float(pdata.get("v", 0.0)),
                             bool(pdata.get("alive", True)), int(pdata.get("score", 0)))
            new_local_players[uname] = st

        # Update pipes
        pipes_list = []
        for pipe in auth_state.get("pipes", []):
            try:
                px = float(pipe.get("x", 0.0))
                gap = float(pipe.get("gap_y", 0.0))
                pipes_list.append(PipeState(px, gap))
            except Exception:
                continue

        # Purge confirmed pending inputs using auth_last_seq
        if auth_last_seq > 0 and self.pending_inputs:
            before = len(self.pending_inputs)
            self.pending_inputs = [pi for pi in self.pending_inputs if pi.seq > auth_last_seq]
            after = len(self.pending_inputs)
            if before != after:
                print(f"[reconcile] Purged {before-after} confirmed inputs (server last_seq={auth_last_seq})")

        # For local player (self), reconcile if authoritative differs significantly
        local_player_pred = self.local_players.get(self.username, PlayerState(self.username))
        dist = abs(local_player_pred.y - auth_y)

        if dist > self.reconcile_threshold or abs(local_player_pred.v - auth_v) > 0.5:
            # Significant difference -> reconcile
            print(f"[reconcile] Significant diff detected: local_y={local_player_pred.y:.2f} auth_y={auth_y:.2f} (dist={dist:.2f})")
            reconciled = PlayerState(self.username, auth_y, auth_v, auth_alive, auth_score)
            # Replay remaining pending inputs only
            for pi in list(self.pending_inputs):
                self.core.step_player(reconciled, pi.flap, pipes_list)
            self.local_players[self.username] = reconciled
        else:
            # Not significant — keep predicted local_y but accept server alive/score to avoid stale death
            if self.username in new_local_players:
                self.local_players.setdefault(self.username, PlayerState(self.username))
                self.local_players[self.username].alive = new_local_players[self.username].alive
                self.local_players[self.username].score = new_local_players[self.username].score

        # Replace other players with authoritative states (so they always show what server says)
        for uname, st in new_local_players.items():
            if uname == self.username:
                continue
            self.local_players[uname] = st

        # Replace pipes
        self.pipes = pipes_list

        # Simple cleanup of pending inputs if buffer grows too large
        if len(self.pending_inputs) > 200:
            self.pending_inputs = self.pending_inputs[-100:]

    # ----- Retrieve leaderboard -----
    def get_leaderboard(self, auth_state: dict):
        if not auth_state:
            return []
        lb = auth_state.get("leaderboard", [])
        cleaned = []
        for entry in lb:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                cleaned.append((str(entry[0]), int(entry[1])))
        return cleaned[:3]


# ----------------- Pygame Rendering & Main Loop -----------------


def run_game(username: str, password: str):
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(f"Flappy Client - {username}")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 24)
    large_font = pygame.font.SysFont(None, 36)

    client = ClientGame(username, password)

    # Discover & login
    ok = client.discover_and_login()
    if not ok and not client.net.logged_in:
        print("[client] Could not login — continuing but not connected.")
    else:
        print("[client] Logged in / connected; starting game.")

    # Initialize local player record
    client.local_players[username] = PlayerState(username)

    # Track time for fixed-tick simulation
    accumulator = 0.0
    last_time = time.time()

    # Main loop
    running = True
    while running:
        now = time.time()
        frame_dt = now - last_time
        last_time = now
        accumulator += frame_dt

        # Event handling
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                client.net.send_json({"type": "disconnect", "username": username})
                client.net.stop()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                    client.net.send_json({"type": "disconnect", "username": username})
                    client.net.stop()
                elif event.key == pygame.K_SPACE:
                    # Flap: register input, send to server, apply prediction immediately
                    client.send_flap()
                elif event.key == pygame.K_r:
                    # Respawn request if dead
                    local = client.local_players.get(username)
                    if local and not local.alive:
                        client.net.send_json({"type": "respawn", "username": username})
                        # immediate local reset for responsiveness
                        client.local_players[username] = PlayerState(username)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                client.send_flap()

        # Fixed-rate client tick(s) for deterministic physics prediction
        while accumulator >= client.client_tick_dt:
            # If there is authoritative state from server, fetch & reconcile
            auth_state = client.net.fetch_state()
            if auth_state:
                client.reconcile_with_authoritative(auth_state)

            # Advance local prediction for one tick (if no new input, apply gravity step)
            client.tick_local()

            # Decrease accumulator
            accumulator -= client.client_tick_dt

        # Rendering (interpolate visually between ticks if desired)
        screen.fill((30, 30, 40))

        # Draw pipes (authoritative positions)
        for pipe in client.pipes:
            rect_top = pygame.Rect(pipe.x, 0, PIPE_WIDTH, pipe.gap_y)
            rect_bottom = pygame.Rect(
                pipe.x, pipe.gap_y + PIPE_GAP, PIPE_WIDTH, SCREEN_HEIGHT - (pipe.gap_y + PIPE_GAP))
            pygame.draw.rect(screen, (60, 180, 75), rect_top)
            pygame.draw.rect(screen, (60, 180, 75), rect_bottom)

        # Draw players
        for uname, pstate in client.local_players.items():
            color = (200, 180, 60) if uname == username else (120, 200, 255)
            pygame.draw.circle(screen, color, (int(BIRD_X), int(pstate.y)), 12)
            name_surf = font.render(
                f"{uname} ({pstate.score})", True, (255, 255, 255))
            screen.blit(name_surf, (BIRD_X + 18, pstate.y - 8))

        # Draw "press R to respawn" when local player is dead
        local = client.local_players.get(username)
        if local and not local.alive:
            resp = large_font.render("You died — press R to respawn", True, (255, 50, 50))
            screen.blit(resp, (SCREEN_WIDTH//2 - resp.get_width()//2, SCREEN_HEIGHT//2 - 20))

        # Draw leaderboard from authoritative state
        auth_state = client.net.fetch_state()
        leaderboard = client.get_leaderboard(auth_state) if auth_state else []
        lb_title = large_font.render("Leaderboard", True, (255, 255, 255))
        screen.blit(lb_title, (SCREEN_WIDTH - 180, 20))
        for i, entry in enumerate(leaderboard):
            txt = font.render(
                f"{i+1}. {entry[0]} — {entry[1]}", True, (255, 255, 255))
            screen.blit(txt, (SCREEN_WIDTH - 180, 60 + i*30))

        # Display status
        status = "Connected" if client.net.logged_in else "Searching..."
        status_surf = font.render(f"{status}", True, (200, 200, 200))
        screen.blit(status_surf, (10, 10))

        instr = font.render(
            "Space / Click = Flap | Esc = Quit", True, (200, 200, 200))
        screen.blit(instr, (10, SCREEN_HEIGHT - 30))

        pygame.display.flip()
        clock.tick(RENDER_FPS)

    pygame.quit()
    print("[client] Exited cleanly.")


# ----------------- Entry Point -----------------
def main():
    print("Flappy Client")
    username = input("Username: ").strip()
    if not username:
        print("Username required.")
        return
    password = input("Password: ").strip()
    run_game(username, password)


if __name__ == "__main__":
    main()
