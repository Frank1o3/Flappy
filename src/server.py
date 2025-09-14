#!/usr/bin/env python3
"""
Flappy Bird Multiplayer Server with SQLite persistence
Users: (id, username, password)
Scores: (id, best, user_id)
"""

import socket
import json
import threading
import time
import sqlite3
from physics_engine import Player, ServerEngine, SCREEN_HEIGHT  # shared physics module

# -------- Config --------
GAME_PORT = 50007
DISCOVERY_PORT = 37020
TICK_RATE = 1/30  # 30 ticks per second
DB_FILE = "flappy_server.db"

# -------- Database Layer --------


class Database:
    def __init__(self, db_file=DB_FILE):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.cur = self.conn.cursor()
        self.setup()

    def setup(self):
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT
            )
        """)
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS Scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                best INTEGER DEFAULT 0,
                user_id INTEGER,
                FOREIGN KEY(user_id) REFERENCES Users(id)
            )
        """)
        self.conn.commit()

    def get_user(self, username):
        self.cur.execute(
            "SELECT id, username, password FROM Users WHERE username=?", (username,))
        return self.cur.fetchone()

    def add_user(self, username, password):
        self.cur.execute(
            "INSERT INTO Users (username, password) VALUES (?, ?)", (username, password))
        uid = self.cur.lastrowid
        self.cur.execute(
            "INSERT INTO Scores (best, user_id) VALUES (?, ?)", (0, uid))
        self.conn.commit()
        return uid

    def validate_user(self, username, password):
        row = self.get_user(username)
        if not row:
            return None
        uid, _, stored_pw = row
        if stored_pw == password:
            return uid
        return None

    def update_score(self, user_id, new_score):
        self.cur.execute("SELECT best FROM Scores WHERE user_id=?", (user_id,))
        row = self.cur.fetchone()
        if row and new_score > row[0]:
            self.cur.execute(
                "UPDATE Scores SET best=? WHERE user_id=?", (new_score, user_id))
            self.conn.commit()

    def get_leaderboard(self, top=3):
        self.cur.execute("""
            SELECT Users.username, Scores.best
            FROM Scores
            JOIN Users ON Scores.user_id = Users.id
            ORDER BY Scores.best DESC
            LIMIT ?
        """, (top,))
        return self.cur.fetchall()


# -------- Server Networking --------
class FlappyServer:
    def __init__(self):
        self.server_engine = ServerEngine()
        self.players: dict[str, Player] = {}
        self.user_ids: dict[str, int] = {}  # username → user_id
        self.running = True

        self.db = Database()

        # Main UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", GAME_PORT))

        # Discovery socket
        self.discovery_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def start(self):
        threading.Thread(target=self.discovery_loop, daemon=True).start()
        threading.Thread(target=self.listen_loop, daemon=True).start()
        self.game_loop()

    def discovery_loop(self):
        while self.running:
            msg = json.dumps({"type": "discovery", "port": GAME_PORT})
            self.discovery_sock.sendto(
                msg.encode(), ("<broadcast>", DISCOVERY_PORT))
            time.sleep(2)

    def listen_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                msg = json.loads(data.decode())
                self.handle_message(msg, addr)
            except Exception as e:
                print("Listen error:", e)

    def handle_message(self, msg, addr):
        mtype = msg.get("type")

        if mtype == "login":
            username, password = msg.get("username"), msg.get("password")
            user = self.db.get_user(username)

            if not user:
                uid = self.db.add_user(username, password)
                print(f"New user registered: {username}")
            else:
                uid = self.db.validate_user(username, password)
                if not uid:
                    self.send(addr, {"type": "login_fail",
                              "reason": "Invalid password"})
                    return

            if username not in self.players:
                self.players[username] = Player(username)

            self.players[username].alive = True
            self.players[username].score = 0
            if uid is not None:
                self.user_ids[username] = uid
            else:
                self.send(addr, {"type": "login_fail",
                          "reason": "User ID is None"})
                return

            # ensure we have a place to store last_seq_received
            if not hasattr(self.players[username], "last_seq_received"):
                self.players[username].last_seq_received = 0

            self.send(addr, {"type": "login_ok", "username": username})
            self.players[username].addr = addr

        elif mtype == "input":
            username = msg.get("username")
            flap = msg.get("flap", False)
            seq = int(msg.get("seq", 0))
            if username in self.players:
                # store flap and the sequence we received; the flap will be used on next tick
                self.players[username].pending_flap = flap
                # store highest seq seen so far for this player
                prev = getattr(self.players[username], "last_seq_received", 0)
                self.players[username].last_seq_received = max(prev, seq)
        elif mtype == "respawn":
            uname = msg.get("username")
            if uname in self.players:
                # Reset player state
                p = self.players[uname]
                p.y = SCREEN_HEIGHT // 2
                p.velocity = 0
                p.alive = True
                p.score = 0
        elif mtype == "disconnect":
            uname = msg.get("username")
            if uname in self.players:
                del self.players[uname]
                print(f"[server] {uname} disconnected")

    def send(self, addr, obj):
        try:
            self.sock.sendto(json.dumps(obj).encode(), addr)
        except Exception as e:
            print("Send error:", e)

    def broadcast(self, obj):
        for p in list(self.players.values()):
            if hasattr(p, "addr"):
                self.send(p.addr, obj)

    def game_loop(self):
        while self.running:
            flaps = {pid: getattr(p, "pending_flap", False)
                     for pid, p in self.players.items()}
            # clear per-tick pending flaps after collecting them
            for p in self.players.values():
                p.pending_flap = False

            self.server_engine.step(self.players, flaps)

            # Save scores for dead players
            for username, p in self.players.items():
                if not p.alive:
                    uid = self.user_ids.get(username)
                    if uid:
                        self.db.update_score(uid, p.score)

            # Build players state and include last_seq_received so clients can purge confirmed inputs
            players_state = {}
            for pid, p in self.players.items():
                players_state[pid] = {
                    "y": p.y,
                    "v": p.velocity,
                    "alive": p.alive,
                    "score": p.score,
                    "last_seq": int(getattr(p, "last_seq_received", 0))
                }

            # Broadcast game state + leaderboard
            leaderboard = self.db.get_leaderboard()
            state = {
                "type": "state",
                "tick": self.server_engine.tick_count,
                "players": players_state,
                "pipes": [{"x": pipe.x, "gap_y": pipe.gap_y} for pipe in self.server_engine.pipes],
                "leaderboard": leaderboard
            }
            self.broadcast(state)

            time.sleep(TICK_RATE)


if __name__ == "__main__":
    server = FlappyServer()
    print(
        f"Server initialized on UDP port {GAME_PORT}. Discovery on UDP port {DISCOVERY_PORT}.")
    server.start()
