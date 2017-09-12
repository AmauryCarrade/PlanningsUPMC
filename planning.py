import click
import json
import pytz
import re
import requests
import time

from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask import Flask, render_template, make_response, abort
from icalendar import Calendar, Event, vDDDTypes, vRecur, vDuration, vText


tz = pytz.timezone('Europe/Paris')


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

                # Because it's a jsonp call there is parenthesis around the JSON value
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

    groups_all = group == 'all' or group == 'tout'
    groups = [group]
    if groups_all:
        groups = get_upmc_public(uni, public_code)['groups']
        group = '_'.join(groups) if groups else '0'

    url = 'http://planning.upmc.fr/ical/{}/{}/{}'.format(uni, public_code, group)
    if url in icals and datetime.now() - datetime.fromtimestamp(icals[url]) < timedelta(minutes=30):
        try:
            with open('cache/{}-{}-{}.ical'.format(uni, public_code, group), 'r') as f:
                pass#return f.read()
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

    ical = fix_upmc_ical('\n'.join(lines), uni=uni, public_code=public_code, groups=groups, remove_groups=not groups_all)

    icals[url] = int(time.time())
    try:
        with open('cache/upmc_icals.json', 'w') as f:
            json.dump(icals, f)
        with open('cache/{}-{}-{}.ical'.format(uni, public_code, group), 'wb') as f:
            f.write(ical)
    except Exception as e:
        raise e

    return ical.decode("utf-8")

def fix_upmc_ical(raw_ical, uni = None, public_code=None, groups=None, remove_groups=True):
    re_group = re.compile(r'(\[[0-9a-zA-Z]+\]) (.+)')
    re_speaker = re.compile(r'Intervenant :([^-]+)')

    ical = Calendar.from_ical(raw_ical)

    calendar_url = None
    if uni and public_code:
        calendar_url = get_upmc_public(uni, public_code)['url']

    calendar_title = 'Calendrier de l\'UPMC'
    if public_code:
        calendar_title += f' pour l\'UE {public_code}'
    if groups:
        calendar_title += f' (groupe{"s" if len(groups) > 1 else ""} {", ".join(groups)})'

    calendar_desc = 'Calendrier de l\'UPMC'
    if public_code:
        calendar_desc += f' pour l\'UE {public_code}'
    if groups:
        calendar_desc += f' ; groupe{"s" if len(groups) > 1 else ""} {", ".join(groups)}.'
    if uni and public_code:
        public = get_upmc_public(uni, public_code)
        calendar_desc += f'\n\nRetrouvez ce calendrier sur le site de l\'UPMC : \n{calendar_url}'

    refresh_interval = vDuration(timedelta(hours=6))

    # Theses ones are in draft but not official yet so they don't validate
    #ical['NAME'] = calendar_title
    #ical['DESCRIPTION'] = calendar_desc
    #ical['URL'] = calendar_url

    # Compatibility
    ical['X-WR-CALNAME'] = calendar_title
    ical['X-WR-CALDESC'] = calendar_desc
    ical['X-WR-TIMEZONE'] = 'Europe/Paris'
    ical['X-PUBLISHED-TTL'] = refresh_interval

    ical['CALSCALE'] = 'GREGORIAN'

    for event in ical.subcomponents:
        if type(event) is not Event:
            continue

        # Fix date (adds timezones)
        for key_date in ['dtstart', 'dtend']:
            tz_aware_date = tz.localize(vDDDTypes.from_ical(event[key_date]))
            event[key_date] = vDDDTypes(tz_aware_date)

        tz_aware_dtstamp = tz.localize(vDDDTypes.from_ical(event['dtstamp']))
        event['dtstamp'] = vDDDTypes(tz_aware_dtstamp.astimezone(pytz.utc))


        # Fix recurrences (adds timezones, too)
        recurrence = vRecur.from_ical(event['rrule'])
        untils = []
        for until in recurrence['until']:  # There can be multiple untils?
            untils.append(tz.localize(until))
        recurrence['until'] = untils

        # Improves title
        title = event['summary']

        course_type = course_type_short = ''
        if 'categories' in event:
            course_type_raw = event['categories'].strip().upper()
            if course_type_raw == 'CM':
                course_type = 'Cours magistral'
                course_type_short = ''
            elif course_type_raw == 'TD':
                course_type = 'Travaux dirigés'
                course_type_short = 'TD'
            elif course_type_raw == 'AUTRE':
                course_type = course_type_short = ''
            else:
                course_type = course_type_short = course_type_raw

        if course_type_short:
            course_type_short += ' '

        group = None
        group_match = re_group.findall(title)
        if group_match:
            group = group_match[0][0]
            title = group_match[0][1]

        title_parts = [part.strip().strip(',') for part in title.split('-')]
        code = name = place = ''
        if len(title_parts) > 0:
            code = title_parts[0]
            if len(title_parts) == 2:
                name = title_parts[0]
                place = title_parts[1]
            else:
                name = title_parts[1]
                place = title_parts[2]

        if ':' in place:
            place = ':'.join(place.split(':')[1:]).strip()

        if remove_groups or group is None: group_title = ''
        else: group_title = group + ' '

        event['summary'] = f'{group_title}{course_type_short}{name}' + (f' ({code})' if code != name else '')

        # Improves description
        old_description = str(event['description'])

        speaker = None
        speaker_match = re_speaker.findall(old_description)
        if speaker_match:
            speaker = speaker_match[0].strip()
        if not speaker:
            speaker = 'inconnu'

        description = f'{course_type}{" : " if course_type else ""}{name} ({code})'
        if group:
            description += f'\nGroupe : {group.strip().replace("[", "").replace("]", "")}'
        description += f'\n\nIntervenant : {speaker}'
        description += f'\n\nSalle : {place}'

        event['description'] = description

    return ical.to_ical()


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
