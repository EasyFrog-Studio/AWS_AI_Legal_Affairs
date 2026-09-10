CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS law_chunks (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024)
);
CREATE TABLE IF NOT EXISTS case_chunks (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024)
);
CREATE TABLE IF NOT EXISTS answer_chunks (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024)
);
CREATE TABLE IF NOT EXISTS law_articles (
  law_article TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS appeal_cases (
  case_id TEXT PRIMARY KEY,
  data JSONB NOT NULL
);
