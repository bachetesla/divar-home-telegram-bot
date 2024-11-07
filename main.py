import requests
import json
import time
import logging
import configparser
import os
import sys
from telegram import Bot
from telegram.error import TelegramError
from requests.exceptions import RequestException, HTTPError, ConnectionError, Timeout
import asyncio

# Load configurations
config = configparser.ConfigParser()
config.read('config.ini')


TELEGRAM_TOKEN = str(config.get('TELEGRAM', 'TOKEN'))
CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')

CITY_IDS = config.get('DIVAR', 'CITY_IDS').split(',')
CATEGORY = config.get('DIVAR', 'CATEGORY')
DISTRICTS = config.get('DIVAR', 'DISTRICTS').split(',')

FETCH_INTERVAL = int(config.getint('SETTINGS', 'FETCH_INTERVAL'))
LOG_LEVEL = config.get('SETTINGS', 'LOG_LEVEL').upper()
MAX_RETRIES = config.getint('SETTINGS', 'MAX_RETRIES')
RETRY_BACKOFF = config.getint('SETTINGS', 'RETRY_BACKOFF')

# Setup logging
logger = logging.getLogger('DivarBot')
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Log to console
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Log to file
file_handler = logging.FileHandler('divar_bot.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Initialize Telegram bot
bot = Bot(token=TELEGRAM_TOKEN)


def fetch_divar_data(last_post_date=None, page=1):
    url = 'https://api.divar.ir/v8/postlist/w/search'

    headers = {
        'Content-Type': 'application/json; charset=UTF-8',
        'Accept': 'application/json, text/plain, */*',
        'Authorization': 'Basic YOUR_AUTHORIZATION_TOKEN',  # Replace with your actual token
        'User-Agent': 'Mozilla/5.0',
        'X-Standard-Divar-Error': 'true',
        'X-Render-Type': 'CSR',
        # Include other headers if necessary
    }

    payload = {
        "city_ids": ["1"],
        "source_view": "FILTER",
        "search_data": {
            "form_data": {
                "data": {
                    "rent": {
                        "number_range": {
                            "maximum": 12000000
                        }
                    },
                    "credit": {
                        "number_range": {
                            "maximum": 200000000
                        }
                    },
                    "districts": {
                        "repeated_string": {
                            "value": ["925","173","167","143","930","920","82","61","86","148","145","146","306","931","929","1031","157","158","75","147","151","152","155","159","78","49","921","172","64","65","168","923","170","139","74","315","90"]
                        }
                    },
                    "category": {
                        "str": {
                            "value": "apartment-rent"
                        }
                    }
                }
            },
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {
                    "data": {
                        "sort": {
                            "str": {
                                "value": "sort_date"
                            }
                        }
                    }
                }
            }
        }
    }

    if last_post_date:
        payload['pagination_data']['last_post_date'] = last_post_date

    # Debug: Print the request details
    print("Request URL:", url)
    print("Request Headers:", headers)
    print("Request Payload:", json.dumps(payload))

    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
    response.raise_for_status()
    data = response.json()
    return data


def load_old_data():
    try:
        with open('./old_data.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            logger.debug("Loaded old data successfully.")
            return data
    except FileNotFoundError:
        logger.info("Old data file not found. Starting fresh.")
        return []
    except Exception as e:
        logger.error(f"Error loading old data: {e}")
        return []


def save_new_data(data):
    try:
        with open('./old_data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            logger.debug("Saved new data successfully.")
    except Exception as e:
        logger.error(f"Error saving new data: {e}")


def get_token(entry):
    return entry.get('data', {}).get('action', {}).get('payload', {}).get('token')


def get_new_entries(old_data, new_data):
    old_ids = set()
    for entry in old_data:
        token = get_token(entry)
        if token:
            old_ids.add(token)
        else:
            logger.warning(f"Token not found in old entry: {entry}")

    new_entries = []
    for entry in new_data:
        token = get_token(entry)
        if token:
            if token not in old_ids:
                new_entries.append(entry)
        else:
            logger.warning(f"Token not found in new entry: {entry}")

    logger.debug(f"Identified {len(new_entries)} new entries.")
    return new_entries


class TelegramException(Exception):
    pass


async def send_updates(entries):
    for entry in entries:
        try:
            post_data = entry.get('data', {})
            title = post_data.get('title', 'No Title')
            middle_description_text = post_data.get('top_description_text', 'No Price')
            price = post_data.get('middle_description_text', 'No Price')
            action_payload = post_data.get('action', {}).get('payload', {})
            district = action_payload.get('web_info', {}).get('district_persian', 'No District')
            token = action_payload.get('token', '')
            url = f"https://divar.ir/v/{token}"

            if any(word in title for word in ["همخونه", "هم", "هم خانه", "اشتراکی"]):
                continue

            if any(word in middle_description_text for word in ["۱۱۱"]) or any(word in price for word in ["۱۱۱"]):
                continue

            message = f"🏠 *{title}*\n" \
                      f"📍 {district}\n" \
                      f"💵 {middle_description_text}\n" \
                      f"💵 {price}\n" \
                      f"🔗 [View Post]({url})"

            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown', disable_web_page_preview=True)
            logger.info(f"Sent update for post: {title}")
            time.sleep(1)  # Respect rate limit
        except TelegramException as e:
            logger.error(f"Telegram error: {e}")
        except Exception as e:
            logger.error(f"Error sending update: {e}")


def fetch_all_pages():
    page = 1
    last_post_date = None
    all_entries = []
    while True:
        logger.debug(f"Fetching page {page}")
        data = fetch_divar_data(last_post_date=last_post_date, page=page)
        if data is None:
            break

        # Adjust the parsing based on the response example
        entries = data.get('list_widgets', [])
        if not entries:
            logger.debug("No more entries found.")
            break

        all_entries.extend(entries)
        last_post_date = entries[-1]['data'].get('action_log', {}).get('server_side_info', {}).get('info', {}).get(
            'sort_date')
        if not last_post_date:
            logger.debug("Last post date not found. Stopping pagination.")
            break
        page += 1
        time.sleep(1)  # Avoid overwhelming the server
    logger.info(f"Fetched total of {len(all_entries)} entries.")

    return all_entries


async def main():
    logger.info("Starting DivarBot...")
    old_data = load_old_data()
    while True:
        try:
            logger.info("Fetching new data...")
            new_data = fetch_all_pages()
            if new_data:
                new_entries = get_new_entries(old_data, new_data)

                if new_entries:
                    logger.info(f"Found {len(new_entries)} new entries. Sending updates...")
                    await send_updates(new_entries)
                    old_data = new_data
                    save_new_data(new_data)
                else:
                    logger.info("No new entries found.")
            else:
                logger.warning("Failed to fetch new data.")
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
        logger.info(f"Waiting for {FETCH_INTERVAL} seconds before next check...")
        time.sleep(FETCH_INTERVAL)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("DivarBot stopped by user.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Critical error: {e}")
        sys.exit(1)
