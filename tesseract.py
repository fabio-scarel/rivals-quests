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
from google.cloud import storage
import gcsfs
import requests


bucket_name = "rivals-quests"

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


def _get_text_from_image(image_url: str):

    response = requests.get(image_url, stream=True)

    if response.status_code != 200:
        raise ValueError(f"Failed to download image: {image_url}")

    # Convert image to a file-like object
    image_bytes = io.BytesIO(response.content)

    # Open image with PIL and extract text
    text = pytesseract.image_to_string(Image.open(image_bytes))

    return text


def _parse_challenge_data(text: str):

    # 2. Time (e.g. "13D 4H")
    time_match = re.search(r'\b(\d+D\s*\d+H)\b', text)
    time_remaining = time_match.group(1) if time_match else None

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
    heroes_pattern = r"\b[A-Z]+(?:[ & ]?[-&]?[ ]?[\n]?[ \n]?[ \n ]?[A-Z]+)*\b"
    all_caps_words = re.findall(heroes_pattern, text)
    # Filter out any uppercase words not considered heroes
    exclusions = {"DEAL", "DAMAGE", "AS", "OR", "SS", "LL", "SSS",
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

    missions_dict_raw = {}
    texts = ""

    for column in images_list:
        texts = texts + _get_text_from_image(column)

    print(texts)
    text = texts.split("\n\n")

    for i in range(0, len(text)):
        missions_dict_raw[i] = _parse_challenge_data(text[i])

    return missions_dict_raw


def get_counters(filtered_result):

    hero_counter = Counter()
    quest_counter = Counter()

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


def sum_mission_count(mission, quest_counter):

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

    results_df['mission_count'] = results_df['mission'].apply(lambda m: sum_mission_count(m, quest_counter))

    results_df['priority'] = results_df['count'] + results_df['mission_count']

    final_df = results_df.sort_values('priority', ascending=False)

    return final_df

storage_client = storage.Client()

@ui.page('/')
class IndexPage:

    def __init__(self):
        """
        The constructor is called once per user session.
        We create *instance variables* instead of using global variables.
        """
        self.hero_counter = Counter()
        self.quest_counter = Counter()
        self.uploaded_images = []
        self.uploaded_file_paths = []
        self.left_drawer_visible = True
        self.fs = gcsfs.GCSFileSystem()
        self.fs.invalidate_cache()
        self.missions_dict_raw={}
        self.final_df = pd.DataFrame()
        self.storage_client = storage.Client()
        self.bucket = storage_client.bucket(bucket_name)

        with ui.row().style('justify-content: space-between; width: 100%; height: 100%; align-items: center;'):
            # This switch controls drawer visibility
            self.toggle_switch = ui.switch('Show Images', value=True, on_change=self.toggle_drawer)

        self.left_drawer = ui.left_drawer(top_corner=True, bottom_corner=True) \
            .style('background-color: #1a1e2c') \
            .bind_visibility_from(self.toggle_switch, 'value') \
            .props('width=358 bordered')

        with self.left_drawer:
            ui.button("Process all uploaded images", on_click=self.process_all)
            ui.upload(
                label='Upload Images',
                auto_upload=True,
                on_upload=self.handle_upload,
                multiple=True
            ).props('accept=".jpeg,.jpg,.png"')

            ui.label("Copy an image and click 'Paste Image' to upload.").style('color: white')
            ui.button('Paste Image', on_click=self.read_clipboard_image)
            self.image_display = ui.image().classes('w-72')

        with ui.row():

            with ui.column():
                # Build your UI
                ui.label('Tutorial').classes('text-lg font-bold mt-4')
                ui.label('Use o screenshot abaixo como template de como tirar o print')
                ui.label('Pode fazer o upload utilizando a caixa de upload ou simplesmente utilizar Win+Shift+S para tirar o print e colar a imagem com o \n botão "Paste Image"')
                ui.label('Tire o print das quests que couber em uma pagina, cole a imagem, desça na pagina de quests, tire o próximo print, cole de novo até acabar a pagina')
                ui.label('Pode fazer vários uploads e depois processar tudo junto')
                ui.label('Para ver o resultado clique em "Process All Uploaded Images"')

                ui.image('https://storage.googleapis.com/rivals-quests/clipboard_image.png').style('width: 800px; height: auto;')

            with ui.column():
                ui.label('Results').classes('text-lg font-bold mt-4')
                # Build a table using your final_df (already loaded somewhere above)
                self.results_table = ui.table(
                    columns=[
                        {'name': 'hero', 'label': 'Hero', 'field': 'hero'},
                        {'name': 'count', 'label': 'Quests Count', 'field': 'count'},
                        {'name': 'role', 'label': 'Role', 'field': 'role'},
                        {'name': 'mission', 'label': 'Bonus Quests', 'field': 'mission'},
                        {'name': 'mission_count', 'label': 'Bonus Count', 'field': 'mission_count', 'classes': 'hidden', 'headerClasses': 'hidden'},
                        {'name': 'priority', 'label': 'Total', 'field': 'priority'},
                    ],
                    rows=self.final_df.to_dict(orient='records'),
                    pagination={
                        'rowsPerPage': 10,
                        'rowsPerPageOptions': [5, 10, 25]
                    }
                )


    def handle_upload(self, e: events.UploadEventArguments):

        self.final_df = pd.DataFrame()
        self.hero_counter = Counter()
        self.quest_counter = Counter()

        unique_filename = f"{uuid.uuid4()}_{e.name}"
        public_url = f"https://storage.googleapis.com/{bucket_name}/{unique_filename}"
        buffer = e.content.read()

        with self.fs.open(f"{bucket_name}/{unique_filename}", 'wb') as f_out:
            f_out.write(buffer)

        self.uploaded_file_paths.append(public_url)
        ui.notify(f"File uploaded")

        left_name = f"{uuid.uuid4()}_left"
        right_name = f"{uuid.uuid4()}_right"

        left_public_url, right_public_url = self.cut_image(public_url, left_name, right_name)

        if left_public_url in self.uploaded_file_paths:
            ui.notify(f"'{public_url}' has already been uploaded!", close_button='OK')
            return

        self.uploaded_images.append(left_public_url)
        self.uploaded_images.append(right_public_url)

    def cut_image(self, public_url, left_name, right_name):

        global bucket_name

        # Extract filename from URL
        filename = public_url.split("/")[-1]

        # Read the image from GCS into memory
        with self.fs.open(f"{bucket_name}/{filename}", "rb") as f:
            image_bytes = f.read()

        # Convert bytes to a file-like object
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size

        left_box = (0, 0, width // 2, height)
        right_box = (width // 2, 0, width, height)

        left_image = image.crop(left_box).convert("RGB")
        right_image = image.crop(right_box).convert("RGB")

        segments = [
            (left_image, left_name),
            (right_image, right_name),
        ]

        public_urls = []
        for cropped_img, name_suffix in segments:

            file_id = str(uuid.uuid4())
            filename_out = f"{file_id}_{name_suffix}.png"
            gcs_path = f"{bucket_name}/{filename_out}"

            buffer = io.BytesIO()
            cropped_img.save(buffer, format="PNG")
            buffer.seek(0)

            with self.fs.open(gcs_path, 'wb') as f:
                f.write(buffer.read())

            public_url_out = f"https://storage.googleapis.com/{bucket_name}/{filename_out}"
            public_urls.append(public_url_out)

        return public_urls[0], public_urls[1]

    def toggle_drawer(self):
        if self.toggle_switch.value:
            self.left_drawer.show()
        else:
            self.left_drawer.hide()

    def process_all(self):

        self.hero_counter = Counter()
        self.quest_counter = Counter()

        self.final_df = pd.DataFrame()

        self.results_table.rows = []

        if not self.uploaded_images:
            ui.notify("No files uploaded yet!", close_button='OK')
            return

        self.final_df = main(self.uploaded_images)

        if self.final_df.empty:
            ui.notify("No data to display!", close_button='OK')
            return
        self.results_table.update_from_pandas(self.final_df)

    async def read_clipboard_image(self):
        """Reads an image from the clipboard and saves it per user session."""

        global bucket_name
        img = await ui.clipboard.read_image()
        if not img:
            ui.notify('You must copy an image to the clipboard first.', close_button='OK')
            return

        unique_filename = f"{uuid.uuid4()}_clipboard_upload.png"
        public_url = f"https://storage.googleapis.com/{bucket_name}/{unique_filename}"

        # Save to GCS
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        with self.fs.open(f"{bucket_name}/{unique_filename}", 'wb') as f_out:
            f_out.write(buffer.read())

        ui.notify("Clipboard image uploaded and saved!", close_button='OK')
        self.uploaded_file_paths.append(public_url)

        # Cut the image
        left_name = f"{uuid.uuid4()}_left"
        right_name = f"{uuid.uuid4()}_right"
        left_public_url, right_public_url = self.cut_image(public_url, left_name, right_name)

        self.uploaded_images.append(left_public_url)
        self.uploaded_images.append(right_public_url)

        # Update UI element that displays the original image
        self.image_display.set_source(public_url)


image_display = ui.image().classes('w-72')

ui.run(host='0.0.0.0', port=8080)
