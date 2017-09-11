import click
import json
import requests
import time

from datetime import datetime, timedelta
from flask import Flask, render_template, make_response, abort
from bs4 import BeautifulSoup


def get_upmc_plannings(force_cache=True, force_update=False, verbose=False):
    '''Loads (if needed) and returns the UPMC plannings'''
    if not force_update:
        try:
            with open('cache/upmc_plannings.json', 'r') as f:
                json_planning = json.load(f)
                if force_cache or datetime.now() - datetime.fromtimestamp(json_planning['datetime']) < timedelta(days=1):
                    return json_planning['plannings']
        except:
            if force_cache:
                abort(503)

    plannings = download_upmc_plannings(verbose)
    with open('cache/upmc_plannings.json', 'w') as f:
        json.dump({'plannings': plannings, 'datetime': int(time.time())}, f)

    return plannings

def download_upmc_plannings(verbose=False):
    plannings = {}

    base_url = 'http://planning.upmc.fr/'
    r = requests.get(base_url)
    r.raise_for_status()

    r.encoding = 'UTF-8'
    soup_home = BeautifulSoup(r.text, 'html.parser')
    for link in soup_home.find_all('a'):
        href = link.get('href')
        if href is None: continue

        planning_name = link.text
        section = href.strip('/')

        if verbose:
            click.echo('Looking for plannings in {}'.format(section))

        planning_url = base_url + href

        r = requests.get(planning_url)
        r.raise_for_status()

        planning_publics = []

        soup_section = BeautifulSoup(r.text, 'html.parser')
        for link in soup_section.find_all('a'):
            href = link.get('href').strip()
            if href is None or href.lower().startswith('http'): continue

            if verbose:
                click.echo('.', nl=False)

            name = link.text
            url = base_url + href.strip('/')
            url_jsoncal = base_url + '/jsoncal.aspx?code={}&groupId='.format(name)

            groups = []
            try:
                r = requests.get(url_jsoncal)
                r.raise_for_status()

                jsoncal = json.loads(r.text.strip('(').strip(')'))

                for event in jsoncal:
                    if event['avaibleGroupId'] not in groups:
                        groups.append(event['avaibleGroupId'])

                groups.sort()
            except Exception as e:
                pass  # no groups :(

            planning_publics.append({
                'name': name,
                'groups': groups,
                'url': url
            })

        planning_publics.sort(key=lambda p: p['name'])

        plannings[section] = {
            'name': planning_name,
            'url': planning_url,
            'publics': planning_publics
        }

        click.echo('\n')

    return plannings

def get_upmc_public(uni, public_code):
    plannings = get_upmc_plannings()
    if uni not in plannings:
        return None
    for public in plannings[uni]['publics']:
        if public['name'] == public_code:
            return public


def get_upmc_ical(uni, public_code, group):
    '''
    Loads (if needed) and returns the iCal file for an UPMC planning
    Group can be a group name or 'all' or 'tout' for all groups
    '''
    icals = {}
    try:
        with open('cache/upmc_icals.json', 'r') as f:
            icals = json.load(f)
    except:
        pass

    if group == 'all' or group == 'tout':
        groups = get_upmc_public(uni, public_code)['groups']
        group = '_'.join(groups) if groups else '0'

    url = 'http://planning.upmc.fr/ical/{}/{}/{}'.format(uni, public_code, group)
    if url in icals and datetime.now() - datetime.fromtimestamp(icals[url]) < timedelta(days=1):
        try:
            with open('cache/{}-{}-{}.ical'.format(uni, public_code, group), 'r') as f:
                return f.read()
        except:
            pass

    r = requests.get(url)

    try:
        r.raise_for_status()
    except:
        return None

    lines_rev = r.text.split('\n')
    lines_rev.reverse() 
    end_index = -1
    for i in range(len(lines_rev)):
        if lines_rev[i].strip().upper() == 'END:VCALENDAR':
            end_index = i
            break

    lines = lines_rev[end_index:]
    lines.reverse()

    icals[url] = int(time.time())
    try:
        with open('cache/upmc_icals.json', 'w') as f:
            json.dump(icals, f)
        with open('cache/{}-{}-{}.ical'.format(uni, public_code, group), 'w') as f:
            f.writelines(lines)
    except Exception as e:
        pass

    return '\n'.join(lines)


app = Flask(__name__)

@app.cli.command()
@click.option('--force', is_flag=True)
@click.option('--quiet', is_flag=True)
def update_plannings(force, quiet):
    get_upmc_plannings(force_cache=False, force_update=force, verbose=not quiet)
    click.echo('Plannings updated.')


@app.route('/')
def index():
    plannings = get_upmc_plannings()
    return render_template('index.html', plannings=plannings)

@app.route('/<uni>-<public_code>-<group>.ical')
def ical(uni, public_code, group):
    ical = get_upmc_ical(uni, public_code, group)
    if ical is None:
        abort(404)

    return make_response(ical, {'Content-Type': 'text/calendar'})
