#!/usr/bin/env python3
"""
Keep Alive Script for Mercury Bot
Simple script to keep the bot running 24/7
"""

import subprocess
import time
import sys
import signal
import os
from datetime import datetime

class KeepAlive:
    def __init__(self):
        self.process = None
        self.running = True
        self.restart_count = 0
        self.max_restarts = 10
        
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\n[{datetime.now()}] Received signal {signum}, shutting down...")
        self.running = False
        if self.process:
            self.process.terminate()
    
    def start_bot(self):
        """Start the bot process"""
        print(f"[{datetime.now()}] Starting Mercury Bot...")
        
        # Start the bot
        self.process = subprocess.Popen(
            [sys.executable, "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        
        print(f"[{datetime.now()}] Bot started with PID: {self.process.pid}")
        return self.process
    
    def monitor_process(self):
        """Monitor the bot process and log output"""
        while self.process.poll() is None and self.running:
            output = self.process.stdout.readline()
            if output:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] [BOT] {output.strip()}")
        
        # Read remaining output
        remaining_output, _ = self.process.communicate()
        if remaining_output:
            for line in remaining_output.split('\n'):
                if line.strip():
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{timestamp}] [BOT] {line.strip()}")
    
    def run(self):
        """Main run loop"""
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        print(f"[{datetime.now()}] 🚀 Starting Mercury Bot Keep Alive")
        print(f"[{datetime.now()}] The bot will automatically restart on crashes")
        
        while self.running and self.restart_count < self.max_restarts:
            try:
                # Start the bot
                self.process = self.start_bot()
                
                # Monitor the process
                self.monitor_process()
                
                # Check if process ended
                return_code = self.process.poll()
                
                if return_code == 0:
                    print(f"[{datetime.now()}] Bot exited gracefully")
                    break
                else:
                    self.restart_count += 1
                    print(f"[{datetime.now()}] ⚠️ Bot crashed with exit code {return_code}")
                    print(f"[{datetime.now()}] Restart attempt {self.restart_count}/{self.max_restarts}")
                    
                    if self.restart_count < self.max_restarts:
                        print(f"[{datetime.now()}] Waiting 30 seconds before restart...")
                        time.sleep(30)
                    else:
                        print(f"[{datetime.now()}] ❌ Maximum restart attempts reached. Stopping.")
                        break
                        
            except KeyboardInterrupt:
                print(f"[{datetime.now()}] Received keyboard interrupt, shutting down...")
                break
            except Exception as e:
                print(f"[{datetime.now()}] ❌ Unexpected error: {e}")
                self.restart_count += 1
                if self.restart_count < self.max_restarts:
                    print(f"[{datetime.now()}] Waiting 30 seconds before restart...")
                    time.sleep(30)
                else:
                    print(f"[{datetime.now()}] ❌ Maximum restart attempts reached. Stopping.")
                    break
        
        print(f"[{datetime.now()}] Keep alive stopped")

if __name__ == "__main__":
    keeper = KeepAlive()
    keeper.run() 