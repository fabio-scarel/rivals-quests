import re
from PIL import Image
import pytesseract
import pandas as pd
from collections import Counter
import numpy as np
from PIL import ImageGrab, Image
from nicegui import ui, events, app
import os
import uuid
from pathlib import Path
import io


missions_dict_raw={}
final_df = pd.DataFrame()
hero_counter = Counter()
quest_counter = Counter()

role_mission_index = {'Vanguards': ['Take', 'Inflict'],
                      'Duelists': ['Inflict', 'Defeat'],
                      'Strategists': 'Heal'}

marvel_rivals_characters = {
    "Vanguards": [
        "BRUCE BANNER", "CAPTAIN AMERICA", "DOCTOR STRANGE",
        "GROOT", "MAGNETO", "PENI PARKER", "THOR", "VENOM"],
    "Duelists": [
        "BLACK PANTHER", "BLACK WIDOW", "HAWKEYE", "HELA", "IRON FIST",
        "IRON MAN","MAGIK", "MISTER FANTASTIC", "MOON KNIGHT", "NAMOR",
        "PSYLOCKE", "THE PUNISHER", "SCARLET WITCH", "SPIDER-MAN",
        "SQUIRREL GIRL", "STAR-LORD", "STORM", "WINTER SOLDIER", "WOLVERINE"],
    "Strategists": [
        "ADAM WARLOCK", "CLOAK & DAGGER", "INVISIBLE WOMAN",
        "JEFF THE LAND SHARK", "LOKI", "LUNA SNOW", "MANTIS",
        "ROCKET RACCOON"]
}

def cut_image(image_path, left_name, right_name):

    image = Image.open(image_path)

    # Get image dimensions
    width, height = image.size

    # Split the image vertically
    left_box = (0, 0, width // 2, height)
    right_box = (width // 2, 0, width, height)

    left_image = image.crop(left_box)
    left_image = left_image.convert('RGB')
    right_image = image.crop(right_box)
    right_image = right_image.convert('RGB')

    left_image.save(f'{left_name}.png')
    right_image.save(f'{right_name}.png')


def _get_text_from_image(image_path: str):
    return pytesseract.image_to_string(Image.open(image_path))


def _parse_challenge_data(text: str):

    # 2. Time (e.g. "13D 4H")
    time_match = re.search(r'\b(\d+D\s*\d+H)\b', text)
    time_remaining = time_match.group(1) if time_match else None

    #    (Optional) If Tesseract sometimes splits "13D" into "13" + "D",
    #    you may want to parse them separately or do more robust checks.

    # 3. Objective: "Deal 15000 Damage" => verb, number, type
    objective_pattern = r'\b([A-Za-z]+)(?:\s+[a-zA-Z]+)?\s+(\d+)\s+(Damage|Enemies|Assists|KO Streak|Health)\b'
    objective_match = re.search(objective_pattern, text, re.IGNORECASE)
    if objective_match:
        objective_verb = objective_match.group(1)
        objective_number = objective_match.group(2)
        objective_type = objective_match.group(3)
    else:
        objective_verb = None
        objective_number = None
        objective_type = None

    # 4. Progress (two captures): "8457 /15000" => current, total
    progress_pattern = r'\b(\d+)\s*/\s*(\d+)\b'
    progress_match = re.search(progress_pattern, text)
    if progress_match:
        progress_current = progress_match.group(1)
        progress_total = progress_match.group(2)
    else:
        progress_current = None
        progress_total = None

    # 6. Exclude known numbers (objective, progress, time if recognized as digits)
    exclude_set = set()

    if objective_number:
        exclude_set.add(objective_number)
    if progress_current:
        exclude_set.add(progress_current)
    if progress_total:
        exclude_set.add(progress_total)

    # 4. Regex for hero names (all-caps words, possibly multiple words)
    heroes_pattern = r"\b[A-Z]+(?:[ & ]?[-&]?[ ]?[\n]?[A-Z]+)*\b"
    all_caps_words = re.findall(heroes_pattern, text)
    # Filter out any uppercase words not considered heroes
    exclusions = {"DEAL", "DAMAGE", "AS", "OR",
                  "ENEMIES", "ASSIST", "KO", "NS", "INFLICT"}
    heroes = [word for word in all_caps_words if word not in exclusions]

    # If Tesseract might pick up "13" or "4" from "13D 4H" as separate digits,
    # exclude them too:
    if time_remaining:
        # extract digits from the time string
        time_digits = re.findall(r'\d+', time_remaining)
        exclude_set.update(time_digits)

    return {
        "time_remaining": time_remaining,
        "objective": {
            "verb": objective_verb,
            "number": objective_number,
            "type": objective_type
        },
        "progress": {
            "current": progress_current,
            "total": progress_total
        },
        "heroes": heroes
    }


def get_missions_from_image(images_list: str):
    texts = ""
    for column in images_list:
        texts = texts + _get_text_from_image(column)

    text = texts.split("\n\n")

    for i in range(0, len(text)):
        missions_dict_raw[i] = _parse_challenge_data(text[i])

    return missions_dict_raw


def get_counters(filtered_result):

    for entry in filtered_result.values():
        try:
            hero_counter.update(entry['heroes'])
            if not len(entry['heroes']):
                quest_counter.update([entry['objective']['verb']])
            else:
                None
        except Exception as e:
            print(f'{e} at {entry}')

    if None in quest_counter:
        quest_counter.pop(None)

    return hero_counter, quest_counter


def adjust_dictionary(missions_dict_raw):

    missions_dict_copy = missions_dict_raw.copy()

    for i, entry in enumerate(missions_dict_copy.values()):
        if 'heroes' in entry:
            entry['heroes'] = [hero.replace("\n", ' ') for hero in entry['heroes']]

            entry['heroes'] = ['THE PUNISHER' if hero == 'THE' else hero for hero in entry['heroes']]
            entry['heroes'] = [np.nan if hero == 'PUNISHER' else hero for hero in entry['heroes']]

            entry['heroes'] = set(entry['heroes'])

        try:
            if entry['time_remaining'] == None and entry['objective']['verb'] == None:
                entry.pop('time_remaining')
                entry.pop('heroes')
                entry.pop('objective')
                missions_dict_copy[i-1]['progress'] = missions_dict_copy[i]['progress']
        except:
            pass

    filtered_missions = {k: v for k, v in missions_dict_copy.items() if set(v.keys()) != {"progress"}}

    return filtered_missions


def get_role(hero):
    for role, heroes in marvel_rivals_characters.items():
        if hero in heroes:
            return role
    return None


def get_mission(hero):
    for mission, roles in role_mission_index.items():
        if hero in marvel_rivals_characters[mission]:
            if isinstance(roles, list):
                return ', '.join(roles)
            else:
                return roles
    return None


def sum_mission_count(mission):

    global quest_counter

    if not isinstance(mission, str):
        return 0
    return sum(1 for quest in quest_counter.keys() if quest in mission)


def get_results(hero_counter):

    filtered_result_df = pd.DataFrame(dict(hero_counter.items()), index=[0]).T.sort_values(0, ascending=False)
    filtered_result_df.reset_index(drop=False, inplace=True)
    filtered_result_df = filtered_result_df.rename(columns={'index': 'hero', 0: 'count'})
    filtered_result_df.dropna(inplace=True)

    return filtered_result_df


def main(images_list):

    missions_dict_raw = get_missions_from_image(images_list)

    missions_dict = adjust_dictionary(missions_dict_raw)

    hero_counter, quest_counter = get_counters(missions_dict)

    results_df = get_results(hero_counter)

    results_df['role'] = results_df['hero'].apply(get_role)

    results_df['mission'] = results_df['hero'].apply(get_mission)

    results_df['mission'] = results_df['mission'].fillna("")
    results_df['mission_count'] = results_df['mission'].apply(sum_mission_count)

    results_df['priority'] = results_df['count'] + results_df['mission_count']

    final_df = results_df.sort_values('priority', ascending=False)

    return final_df

# final_df = main(images_list)

path = Path('photos')
path.mkdir(parents=True, exist_ok=True)

uploaded_file_paths = []
uploaded_images = []

os.makedirs('/tmp/photos', exist_ok=True)
app.add_static_files('/photos', '/tmp/photos')

def handle_upload(e: events.UploadEventArguments):

    global hero_counter, quest_counter, missions_dict_raw

    hero_counter = Counter()
    quest_counter = Counter()

    file_path = path / e.name
    if file_path in uploaded_file_paths:
        ui.notify(f"'{file_path.name}' has already been uploaded!", close_button='OK')
        return

    # Save to disk
    with open(file_path, 'wb') as f:
        f.write(e.content.read())

    uploaded_file_paths.append(file_path)
    print(f"File uploaded: {file_path}")

    left_out = path / f"{file_path.stem}_left"
    right_out = path / f"{file_path.stem}_right"

    cut_image(file_path, left_out, right_out)

    uploaded_images.append(Path(str(left_out)+'.png'))
    uploaded_images.append(Path(str(right_out)+'.png'))

def process_all():

    global hero_counter, quest_counter
    hero_counter = Counter()
    quest_counter = Counter()

    final_df = pd.DataFrame()

    if not uploaded_file_paths:
        ui.notify("No files uploaded yet!", close_button='OK')
        return

    final_df = main(uploaded_images)

    if final_df.empty:
        ui.notify("No data to display!", close_button='OK')
        return

    results_table.update_from_pandas(final_df)

# Function to handle drawer visibility
left_drawer_visible = True
def toggle_drawer():
    if toggle_switch.value:
        left_drawer.show()
    else:
        left_drawer.hide()

ui.label('Results').classes('text-lg font-bold mt-4')
with ui.row().style('justify-content: space-between; width: 100%; height: 100%; align-items: center;'):
    toggle_switch = ui.switch('Show Images', value=left_drawer_visible, on_change=toggle_drawer)

left_drawer = ui.left_drawer(top_corner=True, bottom_corner=True). \
    style('background-color: #1a1e2c').bind_visibility_from(toggle_switch, 'value'). \
        props('width=358 bordered')

with left_drawer:
    ui.button("Process all uploaded images", on_click=process_all)
    ui.upload(
        label='Upload Images',
        auto_upload=True,
        on_upload=handle_upload,
        multiple=True
    ).props('accept=".jpeg,.jpg,.png"')

results_table = ui.table(
        columns=[
            {'name': 'hero', 'label': 'Hero', 'field': 'hero'},
            {'name': 'count', 'label': 'Quests Count', 'field': 'count'},
            {'name': 'role', 'label': 'Role', 'field': 'role'},
            {'name': 'mission', 'label': 'Bonus Quests', 'field': 'mission'},
            {'name': 'mission_count', 'label': 'Bonus Count', 'field': 'mission_count'},
            {'name': 'priority', 'label': 'Total', 'field': 'priority'},
        ],
        rows=final_df.to_dict(orient='records'),
                pagination={
                    'rowsPerPage': 10,
                    'rowsPerPageOptions': [5, 10, 25]
                }
            )

ui.run(host='0.0.0.0', port=8080)
