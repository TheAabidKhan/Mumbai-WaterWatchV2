import os
import re
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from supabase import create_client

# Load environment variables safely
SUPABASE_URL = os.environ.get('SUPABASE_URL')
# Use Service Role Key for backend scripts to bypass RLS policies
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')

SOURCE_URL = 'https://www.mumbailakewaterlevel.in/'
IST = timezone(timedelta(hours=5, minutes=30))
LAKES = ['Bhatsa', 'Upper Vaitarna', 'Middle Vaitarna', 'Modak Sagar', 'Tansa', 'Vihar', 'Tulsi']


def to_number(s):
    return float(s.replace(',', ''))


def get_page_text():
    headers = {'User-Agent': 'Mozilla/5.0 (MumbaiWaterWatch Automated Worker)'}
    r = requests.get(SOURCE_URL, headers=headers, timeout=20)
    r.raise_for_status()
    
    # Use BeautifulSoup instead of risky regex tag stripping
    soup = BeautifulSoup(r.text, 'html.parser')
    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()
        
    text = soup.get_text(separator=' ')
    return re.sub(r'\s+', ' ', text)


def parse(text):
    lakes = []
    for name in LAKES:
        m = re.search(rf'{re.escape(name)}\s*:?\s*(\d+(?:\.\d+)?)\s*%\s*full\s*\(\s*([\d,]+)\s*ML',
                      text, re.I)
        if not m:
            raise ValueError(f'Could not find {name} on the page')
        pct = float(m.group(1))
        if not 0 <= pct <= 100:
            raise ValueError(f'{name} value {pct} looks wrong')
        lakes.append({'lake_name': name, 'percentage': pct, 'stored_ml': to_number(m.group(2))})

    t = re.search(r'Total storage.*?(\d+(?:\.\d+)?)\s*%\s*\(\s*([\d,]+)\s*ML\s*of\s*([\d,]+)\s*ML',
                  text, re.I)
    if not t:
        raise ValueError('Could not find the combined total on the page')
    combined = float(t.group(1))
    total_stored = to_number(t.group(2))
    capacity = to_number(t.group(3))

    # Sanity checks so incorrect numbers never reach Supabase
    if abs(sum(l['stored_ml'] for l in lakes) - total_stored) > 5:
        raise ValueError('Lake volumes do not add up to the page total')
    if abs(total_stored / capacity * 100 - combined) > 0.1:
        raise ValueError('Combined percentage does not match stored volume')

    d = re.search(r'Mumbai Lake Water Levels,?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})', text)
    report_date = d.group(1) if d else 'date not found'
    return lakes, combined, report_date


def main():
    dry_run = '--dry-run' in sys.argv

    if not SUPABASE_URL or not SUPABASE_KEY:
        print('ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables.')
        sys.exit(1)

    try:
        lakes, combined, report_date = parse(get_page_text())
    except Exception as e:
        print(f'FAILED to read the source: {e}')
        print('Nothing was changed in Supabase.')
        sys.exit(1)

    now = datetime.now(IST)
    print(f'Report date on source: {report_date}')
    for l in lakes:
        print(f"  {l['lake_name']:16s} {l['percentage']:6.2f}%")
    print(f'  {"COMBINED":16s} {combined:6.2f}%')

    if dry_run:
        print('Dry run: nothing was saved.')
        return

    label = f'Report: {report_date} · fetched {now:%I:%M %p} IST'
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    new_rows = [{
        'lake_name': l['lake_name'], 
        'percentage': l['percentage'],
        'combined_pct': combined, 
        'last_updated': label
    } for l in lakes]

    try:
        # Safely replace data
        old_response = sb.table('lake_levels').select('id').execute()
        old_ids = [r['id'] for r in old_response.data] if old_response.data else []

        sb.table('lake_levels').insert(new_rows).execute()
        if old_ids:
            sb.table('lake_levels').delete().in_('id', old_ids).execute()

        print(f'Successfully saved to Supabase: {label}')
    except Exception as db_err:
        print(f'Database error during save: {db_err}')
        sys.exit(1)


if __name__ == '__main__':
    main() 