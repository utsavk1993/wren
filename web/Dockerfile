FROM node:22-slim

WORKDIR /app

RUN corepack enable

# Copy the manifest, lockfile and yarn config first so the install layer caches
# on dependency changes rather than on every source edit.
COPY package.json yarn.lock .yarnrc.yml ./
RUN yarn install --immutable

COPY . .

EXPOSE 5173
CMD ["yarn", "dev"]
