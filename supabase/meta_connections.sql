create table if not exists public.meta_connections (
  user_id uuid primary key references auth.users(id) on delete cascade,
  page_id text not null,
  page_name text not null default '',
  page_access_token text not null,
  instagram_user_id text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.meta_connections enable row level security;