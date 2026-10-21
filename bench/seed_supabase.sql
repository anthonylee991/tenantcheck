-- Benchmark ground truth for the Supabase/RLS check (bench/run_supabase.py).
-- Apply to a throwaway local Supabase:
--   npx --yes supabase@latest start
--   docker exec -i supabase_db_<project> psql -U postgres -d postgres < bench/seed_supabase.sql
--
--   public.items         : RLS DISABLED  -> any caller (anon or another user) sees all rows
--   public.items_secure  : RLS ENABLED   -> owner-only policy, the clean twin
--   public.posts         : RLS DISABLED, owner column is `author_id` (not `user_id`)
--                          and has NOT NULL columns -> exercises column heuristics + defaults
--   public.posts_secure  : RLS ENABLED   -> owner-only, the clean twin
--   public.post_titles   : a VIEW -> must be skipped safely (not insertable)
create extension if not exists pgcrypto;

drop view if exists public.post_titles;
drop table if exists public.items;
drop table if exists public.items_secure;
drop table if exists public.posts;
drop table if exists public.posts_secure;

create table public.items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  title text,
  secret text
);

create table public.items_secure (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  title text,
  secret text
);

create table public.posts (
  id uuid primary key default gen_random_uuid(),
  author_id uuid,
  title text not null,
  body text not null,
  published boolean default false
);

create table public.posts_secure (
  id uuid primary key default gen_random_uuid(),
  author_id uuid,
  title text not null,
  body text not null,
  published boolean default false
);

alter table public.items_secure enable row level security;

create policy owner_select on public.items_secure
  for select using (auth.uid() = user_id);
create policy owner_insert on public.items_secure
  for insert with check (auth.uid() = user_id);
create policy owner_update on public.items_secure
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy owner_delete on public.items_secure
  for delete using (auth.uid() = user_id);

alter table public.posts_secure enable row level security;

create policy posts_owner_all on public.posts_secure
  for all using (auth.uid() = author_id) with check (auth.uid() = author_id);

create view public.post_titles as select id, title from public.posts;

grant usage on schema public to anon, authenticated;
grant all on public.items to anon, authenticated;
grant all on public.items_secure to anon, authenticated;
grant all on public.posts to anon, authenticated;
grant all on public.posts_secure to anon, authenticated;
grant select on public.post_titles to anon, authenticated;

select tablename, rowsecurity from pg_tables where schemaname = 'public';

