"""Bridge to IVZ Carbon: pulls Scope 1/2/3 totals per site/year and turns them into Hub measures."""
import os
import httpx

CARBON_API_BASE = os.getenv('CARBON_API_BASE', 'https://ivzcarbon.vercel.app').rstrip('/')

# (internal key, ghgScope, allocationMethod) — mirrors the Hub's ENV-GHG-S1/S2M/S2L/S3 metric filters.
SCOPE_ROWS = [('1', 'Scope 1', None), ('2', 'Scope 2', 'Market-based'),
              ('2L', 'Scope 2', 'Location-based'), ('3', 'Scope 3', None)]


async def carbon_get(path, token, params=None):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(CARBON_API_BASE + path, headers={'Authorization': 'Bearer ' + token}, params=params)
    r.raise_for_status()
    return r.json()


async def fetch_sites(token):
    return await carbon_get('/api/link/sites', token)


def measure_rows(site_id, hub_loc, year, summary):
    scopes = summary.get('scopes', {})
    values = {'1': scopes.get('1', 0), '2': scopes.get('2', 0),
              '2L': summary.get('scope2LocationKg', 0), '3': scopes.get('3', 0)}
    rows = []
    for key, ghg_scope, method in SCOPE_ROWS:
        dims = {'ghgScope': ghg_scope}
        if method:
            dims['allocationMethod'] = method
        rows.append({'id': f'CARBON-{site_id}-{year}-{key}', 'measure': 'CO2E', 'unit': 'tCO2e',
                     'value': round(values[key] / 1000, 6), 'loc': hub_loc, 'y': year,
                     'src': 'IVZ Carbon', 'dims': dims})
    return rows


async def sync_measures(state, site_map, token):
    """Replace CO2E measures for every mapped (location, year) with fresh figures from IVZ Carbon.

    Returns the number of Carbon-sourced rows written. Raises httpx.HTTPError on a connectivity
    or auth failure against Carbon; callers should surface that as a user-facing error.
    """
    periods = await carbon_get('/api/link/periods', token)
    years = sorted({int(p[:4]) for p in periods})
    touched, new_rows = set(), []
    for carbon_site, hub_loc in site_map.items():
        for year in years:
            summary = await carbon_get('/api/summary', token, {'year': year, 'site': carbon_site})
            new_rows.extend(measure_rows(carbon_site, hub_loc, year, summary))
            touched.add((hub_loc, year))
    state['measures'] = [r for r in state['measures']
                         if r.get('measure') != 'CO2E' or (r.get('loc'), r.get('y')) not in touched] + new_rows
    return len(new_rows)
