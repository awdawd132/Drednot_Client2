# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Install system dependencies required for Selenium and Chrome/Chromium
RUN apt-get update && apt-get install -y \
    procps \
    chromium \
    # Add other dependencies if needed by your script
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application's code into the container
COPY . .

# Tell Flask what Python file to run
# Use PYTHONUNBUFFERED to ensure logs are sent straight to Render's log stream
ENV PYTHONUNBUFFERED=1

# Command to run your application
CMD ["python", "drednot_bot.py"]
