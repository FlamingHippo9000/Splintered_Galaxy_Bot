import discord
import signal
import asyncio
import Include.bot_responses as bot_responses
import os
from dotenv import dotenv_values, find_dotenv, load_dotenv
import openai

MAX_CHARS=2000

def get_env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value

#import mysql.connector as connector

def truncate(text):
    return text[:MAX_CHARS]


async def send_message(message, author):
    try:
        to_be_truncated = await bot_responses.handle_response(message, author)
        response = truncate(to_be_truncated)
        #await message.author.send(response) if is_private else await message.channel.send(response)
        await message.channel.send(response)
    except Exception as e:
        print("ERROR: send_message exception: " + str(e))

def run_discord_bot():
    load_dotenv(find_dotenv())
    token = get_env("BOT_TOKEN")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f'{client.user} is now running!')
        bot_responses.ADMIN = str(client.user)
    
    @client.event
    async def on_message(message):
        user_message = ""
        if message.author == client.user:
            return
        author = message.author
        user_message = str(message.content)
        channel =  str(message.channel)

        if len(user_message) != 0:
            if user_message[0] == bot_responses.PREFIX or user_message[0] == bot_responses.PREFIX_OPENAI:
                print(f"{str(author)} said: '{user_message}' in {channel}!")
                user_message = user_message[1:]  # [1:] Removes the '?'
                await send_message(message, author)
    client.run(token)
        
        
        
