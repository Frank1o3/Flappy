"""
server_db.py: Database layer for user authentication and score persistence.
Extracted from server.py.
"""

import sqlite3
from typing import Optional, List, Tuple

DB_FILE = "flappy_server.db"

class Database:
    """Handles all interaction with the SQLite database."""
    def __init__(self, db_file: str = DB_FILE):
        # check_same_thread=False is essential for multi-threading access
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.cur = self.conn.cursor()
        self.setup()

    def setup(self):
        """Creates tables if they don't exist."""
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

    def get_user(self, username: str) -> Optional[Tuple]:
        """Fetches user ID, username, and password."""
        self.cur.execute(
            "SELECT id, username, password FROM Users WHERE username=?", (username,))
        return self.cur.fetchone()

    def add_user(self, username: str, password: str) -> Optional[int]:
        """Creates a new user and returns the new user_id."""
        try:
            self.cur.execute(
                "INSERT INTO Users (username, password) VALUES (?, ?)", (username, password))
            user_id = self.cur.lastrowid
            self.cur.execute(
                "INSERT INTO Scores (user_id, best) VALUES (?, 0)", (user_id,))
            self.conn.commit()
            return user_id
        except sqlite3.IntegrityError:
            return None

    def update_score(self, user_id: int, new_score: int):
        """Updates the best score for a user."""
        self.cur.execute(
            "UPDATE Scores SET best = MAX(best, ?) WHERE user_id=?", (new_score, user_id))
        self.conn.commit()

    def get_leaderboard(self) -> List[Tuple[str, int]]:
        """Fetches the top scores (username, best_score)."""
        self.cur.execute("""
            SELECT U.username, S.best 
            FROM Scores S
            JOIN Users U ON S.user_id = U.id
            ORDER BY S.best DESC
            LIMIT 10
        """)
        return self.cur.fetchall()