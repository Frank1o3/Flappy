#!/usr/bin/env python3
"""
Flappy Bird Multiplayer Server with SQLite persistence
Uses new modular architecture: server_db, data_models, physics_server.
"""

import socket
import json
import threading
import time
from typing import Dict, Tuple

# --- Import from new modular structure ---
from constants import GAME_PORT, DISCOVERY_PORT, TICK_TIME
from data_models import Player
from physics_server import ServerEngine
from server_db import Database

# -------- Server Class --------

class FlappyServer:
    def __init__(self):
        # Database
        self.db = Database()
        self.user_ids: Dict[str, int] = {}  # Map: username -> user_id

        # Network
        self.game_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.game_sock.bind(('', GAME_PORT))
        
        self.discovery_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Game State
        self.players: Dict[str, Player] = {}
        self.player_addrs: Dict[Tuple[str, int], str] = {} # Map: (IP, Port) -> username
        self.server_engine = ServerEngine()

        # Threading
        self.running = threading.Event()
        self.running.set()
        self.network_thread = threading.Thread(target=self._network_loop)
        self.game_thread = threading.Thread(target=self._game_loop)
        self.discovery_thread = threading.Thread(target=self._discovery_loop)
        
    def start(self):
        """Start all server loops."""
        self.network_thread.start()
        self.game_thread.start()
        self.discovery_thread.start()
        
    def stop(self):
        """Stop all server loops."""
        print("Stopping server...")
        self.running.clear()
        self.network_thread.join()
        self.game_thread.join()
        self.discovery_thread.join()
        self.game_sock.close()
        self.discovery_sock.close()
        print("Server stopped.")
        
    def _discovery_loop(self):
        """Broadcasts server presence for clients to find."""
        print(f"Discovery thread started. Broadcasting on port {DISCOVERY_PORT}.")
        msg = json.dumps({"type": "discovery", "port": GAME_PORT}).encode('utf-8')
        while self.running.is_set():
            try:
                self.discovery_sock.sendto(msg, ('<broadcast>', DISCOVERY_PORT))
                time.sleep(2)  # Broadcast every 2 seconds
            except Exception as e:
                if self.running.is_set():
                    print(f"Discovery error: {e}")
                
    def _network_loop(self):
        """Listens for and processes incoming UDP packets (login, input)."""
        print(f"Network thread started. Listening on port {GAME_PORT}.")
        while self.running.is_set():
            try:
                data, addr = self.game_sock.recvfrom(65536)
                message = json.loads(data.decode('utf-8'))
                
                msg_type = message.get("type")
                username = message.get("username")
                
                if msg_type == "login":
                    self._handle_login(message, addr)
                elif msg_type == "input" and username in self.players:
                    self._handle_input(message, username)
                else:
                    self._send_error(addr, "unknown_message", "Invalid message type or user not logged in.")
                    
            except socket.timeout:
                continue
            except ConnectionResetError:
                if addr:
                    self._handle_disconnect(addr)
                else:
                    print("ConnectionResetError on socket before address was assigned.")
            except Exception as e:
                if self.running.is_set():
                    print(f"Network processing error: {e}")

    def _handle_login(self, message: dict, addr: Tuple[str, int]):
        """Authenticates user and adds them to the game state."""
        username = message["username"]
        password = message["password"]
        
        user_data = self.db.get_user(username)

        if user_data is None:
            user_id = self.db.add_user(username, password)
            if user_id is None:
                return self._send_error(addr, "login_failed", "Registration failed (username taken).")
            print(f"New user registered: {username}")
        else:
            user_id, _, stored_password = user_data
            if stored_password != password:
                return self._send_error(addr, "login_failed", "Incorrect password.")
        
        # Successful Login/Registration
        if username not in self.players:
            self.players[username] = Player(id=username, addr=addr)
            self.user_ids[username] = user_id
            self.player_addrs[addr] = username
            print(f"Player {username} logged in from {addr}")
        
        # Send a confirmation back to the client
        self.game_sock.sendto(json.dumps({
            "type": "login_success", 
            "username": username,
            "server_tick_rate": int(1/TICK_TIME)
        }).encode('utf-8'), addr)

    def _handle_input(self, message: dict, username: str):
        """Processes player input (flap)."""
        player = self.players.get(username)
        if player:
            player.pending_flap = message.get("flap", False)
            player.last_seq_received = message.get("seq", 0)

    def _handle_disconnect(self, addr: Tuple[str, int]):
        """Clean up state for a disconnected player."""
        username = self.player_addrs.pop(addr, None)
        if username and username in self.players:
            player = self.players.pop(username)
            print(f"Player {username} disconnected from {addr}. Final score: {player.score}")
            self.db.update_score(self.user_ids.get(username, -1), player.score)

    def _send_error(self, addr: Tuple[str, int], error_type: str, message: str):
        """Sends an error message to a client."""
        error_msg = json.dumps({"type": error_type, "message": message}).encode('utf-8')
        self.game_sock.sendto(error_msg, addr)
        print(f"Sent error to {addr}: {message}")

    def broadcast(self, message: dict):
        """Sends the game state to all connected players."""
        data = json.dumps(message).encode('utf-8')
        addrs = [p.addr for p in self.players.values() if p.addr]
        
        for addr in addrs:
            try:
                self.game_sock.sendto(data, addr)
            except Exception as e:
                print(f"Error broadcasting to {addr}: {e}")

    def _game_loop(self):
        """The main authoritative game loop running at the fixed tick rate."""
        print(f"Game thread started. Tick rate: {1/TICK_TIME} Hz.")
        while self.running.is_set():
            start_time = time.time()
            
            # 1. Collect inputs for the current tick
            flaps_this_tick = {}
            for username, p in self.players.items():
                flaps_this_tick[username] = p.pending_flap
                p.pending_flap = False

            # 2. Run the authoritative physics step
            self.server_engine.step(self.players, flaps_this_tick)

            # 3. Save scores for dead players & prepare state for broadcast
            players_state = {}
            for username, p in self.players.items():
                if not p.alive:
                    uid = self.user_ids.get(username)
                    if uid:
                        self.db.update_score(uid, p.score)
                
                players_state[username] = p.to_client_state()

            # 4. Build and Broadcast game state + leaderboard
            leaderboard = self.db.get_leaderboard()
            state = {
                "type": "state",
                "tick": self.server_engine.tick_count,
                "players": players_state,
                "pipes": [
                    {"x": round(pipe.x, 2), "gap_y": round(pipe.gap_y, 2)} 
                    for pipe in self.server_engine.pipes
                ],
                "leaderboard": leaderboard
            }
            self.broadcast(state)

            # 5. Time remaining until next tick
            elapsed_time = time.time() - start_time
            sleep_time = TICK_TIME - elapsed_time
            if sleep_time > 0:
                time.sleep(sleep_time)


if __name__ == "__main__":
    import sys, os
    if os.path.basename(os.getcwd()) == 'src':
        sys.path.insert(0, os.path.abspath('..'))
        
    server = FlappyServer()
    print(
        f"Server initialized on UDP port {GAME_PORT}. Discovery on UDP port {DISCOVERY_PORT}.")
    try:
        server.start()
        while server.running.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        server.stop()