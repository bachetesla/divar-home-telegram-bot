# Use an official lightweight Python image
FROM python:latest

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Expose the port (if necessary)
# EXPOSE 8000

# Set the command to run your application
CMD ["python", "main.py"]
