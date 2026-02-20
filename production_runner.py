#!/usr/bin/env python3
"""
Production Runner for Mercury Bot
Keeps the bot running 24/7 with automatic restarts and monitoring
"""

import asyncio
import signal
import sys
import time
import logging
from datetime import datetime
import subprocess
import os
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mercury_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class ProductionRunner:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.restart_count = 0
        self.max_restarts = 10
        self.restart_delay = 60  # seconds
        self.running = True
        
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("Process didn't terminate gracefully, forcing kill")
                self.process.kill()
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def start_bot(self) -> subprocess.Popen:
        """Start the bot process"""
        logger.info("Starting Mercury Bot...")
        
        # Start the bot with proper environment
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'  # Ensure output is not buffered
        
        process = subprocess.Popen(
            [sys.executable, 'main.py'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        logger.info(f"Bot started with PID: {process.pid}")
        return process
    
    def monitor_process(self, process: subprocess.Popen):
        """Monitor the bot process and log output"""
        while process.poll() is None and self.running:
            output = process.stdout.readline()
            if output:
                # Log the output with timestamp
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"[BOT] {output.strip()}")
        
        # Read any remaining output
        remaining_output, _ = process.communicate()
        if remaining_output:
            for line in remaining_output.split('\n'):
                if line.strip():
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"[BOT] {line.strip()}")
    
    def run(self):
        """Main run loop - keeps the bot running 24/7"""
        self.setup_signal_handlers()
        
        logger.info("🚀 Starting Mercury Bot Production Runner")
        logger.info("The bot will automatically restart on crashes")
        
        while self.running and self.restart_count < self.max_restarts:
            try:
                # Start the bot
                self.process = self.start_bot()
                
                # Monitor the process
                self.monitor_process(self.process)
                
                # Check if process ended
                return_code = self.process.poll()
                
                if return_code == 0:
                    logger.info("Bot exited gracefully")
                    break
                else:
                    self.restart_count += 1
                    logger.warning(f"Bot crashed with exit code {return_code}")
                    logger.info(f"Restart attempt {self.restart_count}/{self.max_restarts}")
                    
                    if self.restart_count < self.max_restarts:
                        logger.info(f"Waiting {self.restart_delay} seconds before restart...")
                        time.sleep(self.restart_delay)
                    else:
                        logger.error("Maximum restart attempts reached. Stopping.")
                        break
                        
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                self.restart_count += 1
                if self.restart_count < self.max_restarts:
                    logger.info(f"Waiting {self.restart_delay} seconds before restart...")
                    time.sleep(self.restart_delay)
                else:
                    logger.error("Maximum restart attempts reached. Stopping.")
                    break
        
        logger.info("Production runner stopped")

def create_systemd_service():
    """Create a systemd service file for the bot"""
    service_content = """[Unit]
Description=Mercury Bot Discord Bot
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/mercury-bot
Environment=PATH=/path/to/mercury-bot/venv/bin
ExecStart=/path/to/mercury-bot/venv/bin/python production_runner.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    
    with open('mercury-bot.service', 'w') as f:
        f.write(service_content)
    
    print("Created mercury-bot.service file")
    print("To install as systemd service:")
    print("1. Update the paths in mercury-bot.service")
    print("2. Copy to /etc/systemd/system/")
    print("3. Run: sudo systemctl enable mercury-bot")
    print("4. Run: sudo systemctl start mercury-bot")

def create_dockerfile():
    """Create a Dockerfile for containerized deployment"""
    dockerfile_content = """FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    gcc \\
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
    CMD python -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Run the production runner
CMD ["python", "production_runner.py"]
"""
    
    with open('Dockerfile', 'w') as f:
        f.write(dockerfile_content)
    
    print("Created Dockerfile")
    print("To build and run with Docker:")
    print("docker build -t mercury-bot .")
    print("docker run -d --name mercury-bot mercury-bot")

def create_docker_compose():
    """Create docker-compose.yml for easy deployment"""
    compose_content = """version: '3.8'

services:
  mercury-bot:
    build: .
    container_name: mercury-bot
    restart: unless-stopped
    environment:
      - MERCURY_API_KEY=${MERCURY_API_KEY}
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - DISCORD_CHANNEL_ID=${DISCORD_CHANNEL_ID}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
"""
    
    with open('docker-compose.yml', 'w') as f:
        f.write(compose_content)
    
    print("Created docker-compose.yml")
    print("To run with docker-compose:")
    print("docker-compose up -d")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "create-systemd":
            create_systemd_service()
        elif command == "create-docker":
            create_dockerfile()
            create_docker_compose()
        elif command == "help":
            print("Available commands:")
            print("  create-systemd  - Create systemd service file")
            print("  create-docker   - Create Dockerfile and docker-compose.yml")
            print("  help            - Show this help message")
            print("  (no args)       - Run the production runner")
        else:
            print(f"Unknown command: {command}")
            print("Use 'help' to see available commands")
    else:
        # Run the production runner
        runner = ProductionRunner()
        runner.run() 