#!/usr/bin/env python3
"""
flappy_client.py

Client with prediction + reconciliation and pygame rendering.
Uses new modular architecture: constants, data_models, physics_client.
"""

import socket
import json
import threading
import time
import pygame
from constants import PIPE_GAP
from typing import Dict, List, Tuple, Optional

# --- Import from new modular structure ---
from .constants import (
    GAME_PORT, DISCOVERY_PORT, BUFFER_SIZE, SERVER_DISCOVERY_TIMEOUT, 
    SERVER_TICK_RATE, CLIENT_TICK_RATE, SCREEN_WIDTH, SCREEN_HEIGHT, 
    BIRD_X, PIPE_WIDTH
)
from .data_models import ClientPlayerState, ClientPipeState
from .physics_client import ClientEngine

# RENDER_FPS can be faster than SERVER_TICK_RATE for smooth rendering
RENDER_FPS = 60

# ----------------- Network Client (discovery / login / state) -----------------

class NetworkClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        
        self.logged_in = False
        self.server_addr: Optional[Tuple[str, int]] = None
        self.server_tick_time = 1.0 / SERVER_TICK_RATE # Default

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.1)

        self.discovery_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.discovery_sock.bind(('', DISCOVERY_PORT))
        self.discovery_sock.settimeout(0.5)

        self.authoritative_state: Dict = {}
        self.state_lock = threading.Lock()
        
        self.running = threading.Event()
        self.running.set()
        self.network_thread = threading.Thread(target=self._network_loop)

    def start(self):
        self.network_thread.start()

    def stop(self):
        self.running.clear()
        self.network_thread.join()
        self.sock.close()
        self.discovery_sock.close()

    def discover_server(self) -> Optional[Tuple[str, int]]:
        """Listens for the server's broadcast on the discovery port."""
        print(f"Searching for server on broadcast port {DISCOVERY_PORT}...")
        start_time = time.time()
        while time.time() - start_time < SERVER_DISCOVERY_TIMEOUT:
            try:
                data, addr = self.discovery_sock.recvfrom(BUFFER_SIZE)
                message = json.loads(data.decode('utf-8'))
                if message.get("type") == "discovery":
                    server_ip = addr[0]
                    game_port = message.get("port", GAME_PORT)
                    print(f"Server discovered at {server_ip}:{game_port}")
                    self.server_addr = (server_ip, game_port)
                    return self.server_addr
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Discovery error: {e}")
        return None

    def login(self):
        """Attempts to log in or register with the discovered server."""
        if not self.server_addr:
            return False

        login_msg = json.dumps({
            "type": "login",
            "username": self.username,
            "password": self.password
        }).encode('utf-8')

        try:
            self.sock.sendto(login_msg, self.server_addr)
            
            start_time = time.time()
            while time.time() - start_time < 3.0:
                try:
                    data, _ = self.sock.recvfrom(BUFFER_SIZE)
                    message = json.loads(data.decode('utf-8'))
                    if message.get("type") == "login_success" and message.get("username") == self.username:
                        print("Login successful.")
                        self.logged_in = True
                        self.server_tick_time = 1.0 / message.get("server_tick_rate", 30)
                        return True
                    elif message.get("type") in ["login_failed", "unknown_message"]:
                        print(f"Login failed: {message.get('message', 'Unknown reason')}")
                        return False
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Error receiving login response: {e}")
                    return False
        except Exception as e:
            print(f"Error sending login request: {e}")
        return False

    def send_input(self, flap: bool, seq: int):
        """Sends the user's input to the server."""
        if not self.logged_in or not self.server_addr:
            return

        input_msg = json.dumps({
            "type": "input",
            "username": self.username,
            "flap": flap,
            "seq": seq
        }).encode('utf-8')
        
        try:
            self.sock.sendto(input_msg, self.server_addr)
        except Exception as e:
            print(f"Error sending input: {e}")

    def _network_loop(self):
        """Dedicated thread to receive authoritative game state from the server."""
        print("Client network thread started.")
        while self.running.is_set():
            if not self.logged_in:
                time.sleep(0.5)
                continue
            
            try:
                data, _ = self.sock.recvfrom(BUFFER_SIZE)
                message = json.loads(data.decode('utf-8'))
                
                if message.get("type") == "state":
                    with self.state_lock:
                        self.authoritative_state = message
            except socket.timeout:
                continue
            except Exception as e:
                if self.running.is_set():
                    print(f"Error receiving state: {e}")
    
    def fetch_state(self) -> Dict:
        """Safely retrieve the latest authoritative state."""
        with self.state_lock:
            return self.authoritative_state.copy()


# ----------------- Game Client (rendering / prediction) -----------------

class FlappyClient:
    def __init__(self, username: str):
        pygame.init()
        self.username = username
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(f"Flappy Bird Multiplayer: {username}")
        
        self.net = NetworkClient(username, password="123")
        
        # --- Game Logic ---
        self.client_engine = ClientEngine()
        self.local_player = ClientPlayerState(username=username)
        self.other_players: Dict[str, ClientPlayerState] = {}
        self.local_pipes: List[ClientPipeState] = []
        
        # Input & Prediction State
        self.input_sequence = 0
        self.input_buffer: Dict[int, bool] = {}
        self.flapped_on_render_frame = False
        
        # Time Management
        self.clock = pygame.time.Clock()
        self.prediction_timer = 0.0
        self.render_delta_time = 0.0
        
    def run(self):
        """The main client execution loop."""
        
        if not self.net.discover_server():
            print("Could not find server. Exiting.")
            pygame.quit()
            return
            
        if not self.net.login():
            print("Login failed. Exiting.")
            pygame.quit()
            return

        self.net.start()
        
        running = True
        while running:
            self.render_delta_time = self.clock.tick(RENDER_FPS) / 1000.0

            # Handle Pygame Events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                if (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE) or event.type == pygame.MOUSEBUTTONDOWN:
                    self.flapped_on_render_frame = True
                
                if event.type == pygame.KEYDOWN and event.key == pygame.K_r and not self.local_player.alive:
                    self.flapped_on_render_frame = True

            # --- Prediction Loop (Fixed Timestep) ---
            self.prediction_timer += self.render_delta_time
            
            while self.prediction_timer >= self.net.server_tick_time:
                self.prediction_timer -= self.net.server_tick_time
                self._prediction_step()
                
            # --- Reconciliation & Render ---
            self._reconcile_state()
            self._draw_game()

        self.net.stop()
        pygame.quit()
        
    def _prediction_step(self):
        """Runs the local prediction one server tick forward."""
        
        is_flap = self.flapped_on_render_frame
        self.flapped_on_render_frame = False

        self.input_sequence += 1
        self.input_buffer[self.input_sequence] = is_flap
        
        self.net.send_input(is_flap, self.input_sequence)
        
        if self.local_player.alive:
            self.client_engine.step_player(
                self.local_player, is_flap, self.local_pipes) 

        self.client_engine.step_pipes(self.local_pipes)
        
    def _reconcile_state(self):
        """Fetches the authoritative state and corrects local prediction errors."""
        auth_state = self.net.fetch_state()
        if not auth_state:
            return

        auth_player_data = auth_state["players"].get(self.username)
        if not auth_player_data:
            return

        auth_y = auth_player_data["y"]
        auth_v = auth_player_data["v"]
        auth_alive = auth_player_data["alive"]
        auth_score = auth_player_data["score"]
        last_seq_ack = auth_player_data["last_seq"]
        
        # 1. Apply authoritative state to local player
        if not self.local_player.alive and auth_alive:
            # Respawn Reconciliation
            self.local_player.y = auth_y
            self.local_player.v = auth_v
            self.local_player.alive = auth_alive
            self.local_player.score = auth_score
            self.input_buffer = {}
        elif self.local_player.alive:
            # Standard Reconciliation (Positional Correction)
            error_y = auth_y - self.local_player.y
            if abs(error_y) > 1.0:
                self.local_player.y = auth_y
                self.local_player.v = auth_v
                
            # Server is always correct on life/score
            self.local_player.alive = auth_alive
            self.local_player.score = auth_score
        
        # 2. Reconciliation/Re-simulation
        if last_seq_ack > 0:
            inputs_to_re_simulate = sorted([s for s in self.input_buffer.keys() if s > last_seq_ack])
            
            for seq in list(self.input_buffer.keys()):
                if seq <= last_seq_ack:
                    del self.input_buffer[seq]

            for seq in inputs_to_re_simulate:
                flap = self.input_buffer[seq]
                self.client_engine.step_player(self.local_player, flap, self.local_pipes)
        
        # 3. Update World State (Pipes)
        self.local_pipes = [
            ClientPipeState(x=p["x"], gap_y=p["gap_y"]) for p in auth_state["pipes"]
        ]

        # 4. Update Other Players
        self.other_players.clear()
        for username, data in auth_state["players"].items():
            if username != self.username:
                self.other_players[username] = ClientPlayerState(
                    username=username,
                    y=data["y"],
                    v=data["v"],
                    alive=data["alive"],
                    score=data["score"]
                )

    def _draw_game(self):
        """Renders the game state using Pygame."""
        
        screen = self.screen
        screen.fill((0, 191, 255))
        white = (255, 255, 255)
        
        large_font = pygame.font.Font(None, 40)
        font = pygame.font.Font(None, 24)

        # Draw Pipes
        pipe_color = (0, 150, 0)
        for pipe in self.local_pipes:
            bottom_y = pipe.gap_y + PIPE_GAP // 2
            bottom_height = SCREEN_HEIGHT - bottom_y
            pygame.draw.rect(screen, pipe_color, (pipe.x, bottom_y, PIPE_WIDTH, bottom_height))
            
            top_height = pipe.gap_y - PIPE_GAP // 2
            pygame.draw.rect(screen, pipe_color, (pipe.x, 0, PIPE_WIDTH, top_height))
            
        # Draw Other Players
        for username, player in self.other_players.items():
            if player.alive:
                pygame.draw.circle(screen, (255, 50, 50), (BIRD_X, int(player.y)), 15)
                name_tag = font.render(username, True, white)
                screen.blit(name_tag, (BIRD_X - name_tag.get_width()//2, player.y - 30))
        
        # Draw Local Player
        color = (0, 255, 0) if self.local_player.alive else (100, 100, 100)
        pygame.draw.circle(screen, color, (BIRD_X, int(self.local_player.y)), 18)
        
        # HUD
        score_text = large_font.render(
            f"Score: {self.local_player.score}", True, white)
        screen.blit(score_text, (SCREEN_WIDTH // 2 - score_text.get_width() // 2, 20))

        if not self.local_player.alive:
            resp = large_font.render("You died — press SPACE/CLICK to respawn", True, (255, 50, 50))
            screen.blit(resp, (SCREEN_WIDTH//2 - resp.get_width()//2, SCREEN_HEIGHT//2 - 20))

        # Leaderboard
        auth_state = self.net.fetch_state()
        leaderboard = auth_state.get("leaderboard", [])
        lb_title = large_font.render("Leaderboard", True, white)
        screen.blit(lb_title, (SCREEN_WIDTH - 180, 20))
        for i, entry in enumerate(leaderboard):
            txt = font.render(
                f"{i+1}. {entry[0]} — {entry[1]}", True, white)
            screen.blit(txt, (SCREEN_WIDTH - 180, 60 + i*30))

        status = "Connected" if self.net.logged_in else "Searching..."
        status_surf = font.render(f"{status}", True, (200, 200, 200))
        screen.blit(status_surf, (10, 10))

        instr = font.render(
            "Space / Click = Flap | Esc = Quit | R = Respawn (if dead)", True, (200, 200, 200))
        screen.blit(instr, (10, SCREEN_HEIGHT - 30))
        
        pygame.display.flip()


if __name__ == "__main__":
    import sys, os
    if os.path.basename(os.getcwd()) == 'src':
        sys.path.insert(0, os.path.abspath('..'))

    test_username = input("Enter your username: ") or f"Player{time.time() * 1000 % 1000:0.0f}"
    
    client = FlappyClient(test_username)
    client.run()