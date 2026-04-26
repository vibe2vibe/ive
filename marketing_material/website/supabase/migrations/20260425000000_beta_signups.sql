-- =============================================
-- IVE Marketing Site — Full Idempotent Migration
-- Safe to run multiple times
-- =============================================

-- CLEANUP: drop everything first so we can recreate cleanly
drop function if exists get_site_content();
drop function if exists verify_pin(text);
drop policy if exists "Anyone can sign up for beta" on beta_signups;
drop index if exists idx_beta_signups_email;
drop table if exists site_pages;
drop table if exists site_config;
drop table if exists beta_signups;
-- Note: can't delete storage buckets via SQL — use the Storage UI if needed

-- =============================================
-- 1. Beta signups
-- =============================================
create table beta_signups (
  id bigint generated always as identity primary key,
  email text not null unique,
  signed_up_at timestamptz not null default now(),
  nda_accepted boolean not null default false,
  nda_accepted_at timestamptz,
  ip_address text,
  user_agent text,
  referrer text
);

alter table beta_signups enable row level security;

create policy "Anyone can sign up for beta"
  on beta_signups for insert to anon
  with check (true);

create index idx_beta_signups_email on beta_signups (email);

-- =============================================
-- 2. Site config (PIN + secrets, never readable by anon)
-- =============================================
create table site_config (
  key text primary key,
  value text not null
);

alter table site_config enable row level security;

insert into site_config (key, value) values ('pin', '77');
insert into site_config (key, value) values ('video_url', '');

-- =============================================
-- 3. Protected site content (HTML stored server-side)
-- =============================================
create table site_pages (
  slug text primary key,
  html text not null,
  updated_at timestamptz not null default now()
);

alter table site_pages enable row level security;

-- =============================================
-- 4. RPC: verify PIN (returns boolean)
-- =============================================
create or replace function verify_pin(attempt text)
returns boolean
language sql
security definer
as $$
  select exists(select 1 from site_config where key = 'pin' and value = attempt);
$$;

grant execute on function verify_pin(text) to anon;

-- =============================================
-- 5. RPC: get site content
--    Returns JSON { html } with {{VIDEO_URL}} replaced
--    Only callable, not directly readable
-- =============================================
create or replace function get_site_content()
returns json
language plpgsql
security definer
as $$
declare
  page_html text;
  vid_url text;
  final_html text;
begin
  select html into page_html from site_pages where slug = 'main';

  if page_html is null then
    return json_build_object('html', null);
  end if;

  select value into vid_url from site_config where key = 'video_url';

  if vid_url is not null and vid_url <> '' then
    final_html := replace(page_html, '{{VIDEO_URL}}', vid_url);
  else
    final_html := replace(page_html, '{{VIDEO_URL}}', '');
  end if;

  return json_build_object('html', final_html);
end;
$$;

grant execute on function get_site_content() to anon;

-- =============================================
-- 7. RPC: waitlist count (91 base + actual signups)
-- =============================================
create or replace function get_waitlist_count()
returns int
language sql
security definer
as $$
  select 91 + count(*)::int from beta_signups;
$$;

grant execute on function get_waitlist_count() to anon;

-- =============================================
-- 6. Storage bucket (public so <video> can fetch directly)
--    URL only revealed after PIN verification via RPC
-- =============================================
insert into storage.buckets (id, name, public)
values ('site-assets', 'site-assets', true)
on conflict (id) do nothing;

-- =============================================
-- DONE. Next steps after running this:
--
-- 1. Upload ive-promo.mp4 to Storage → site-assets bucket
--
-- 2. Set video URL:
--    UPDATE site_config SET value = 'https://vjqdpsdcxgusnmemsnsq.supabase.co/storage/v1/object/public/site-assets/ive-promo.mp4' WHERE key = 'video_url';
--
-- 3. Insert site HTML (use dollar-quoting to avoid escaping):
--    INSERT INTO site_pages (slug, html) VALUES ('main', $site$
--    <paste contents of site_content_upload.html>
--    $site$);
-- =============================================
