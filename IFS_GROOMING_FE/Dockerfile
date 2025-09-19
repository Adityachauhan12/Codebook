# Use a minimal Node.js base image
FROM node:18-alpine AS base

# Set working directory
WORKDIR /app

# Install only production dependencies
COPY package.json package-lock.json ./
RUN npm install --omit=dev

# Copy rest of the app
COPY . .

# Expose Vite dev server port
EXPOSE 5173

# Run Vite dev server with external access
CMD ["npm", "run", "dev", "--", "--host"]
