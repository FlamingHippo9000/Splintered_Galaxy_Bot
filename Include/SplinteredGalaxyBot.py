import discord
import Include.bot_responses as bot_responses
from dotenv import load_dotenv
import os
from dotenv import dotenv_values, find_dotenv
import openai


MAX_CHARS=2000

#import mysql.connector as connector

def truncate(text):
    return text[:MAX_CHARS]


async def send_message(message, author):
    try:
        response = truncate(bot_responses.handle_response(message, author))
        #await message.author.send(response) if is_private else await message.channel.send(response)
        await message.channel.send(response)
    except Exception as e:
        print(e)

def get_env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value

def run_discord_bot():
    load_dotenv(find_dotenv())
    token = get_env("BOT_TOKEN")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        # try:
        #     connector.connect()
        # except Exception as e:
        #     print(e)
        print(f'{client.user} is now running!')
        bot_responses.ADMIN = str(client.user)
    
    @client.event
    async def on_message(message):
        if message.author == client.user: 
            author = message.author
            user_message = str(message.content)
            channel =  str(message.channel)

        if len(user_message) != 0:
            if user_message[0] == bot_responses.PREFIX or user_message[0] == bot_responses.PREFIX_OPENAI:
                print(f"{str(author)} said: '{user_message}' in {channel}!")
                user_message = user_message[1:]  # [1:] Removes the '?'
                await send_message(message, author)
        
        
        



    client.run(token)