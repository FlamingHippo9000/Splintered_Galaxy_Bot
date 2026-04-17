import discord

#For cooldown and timekeeping
from datetime import datetime
from datetime import timedelta
from openai import OpenAI
import os

from dotenv import load_dotenv
from dotenv import dotenv_values, find_dotenv

load_dotenv(find_dotenv())

def get_env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value

openai_key = get_env("OPENAI_KEY")

#Bot Prefix
PREFIX = '?'
PREFIX_OPENAI = "~"

#Ignore for now probably can delete later
ADMIN = ''

#Work Cooldown
COOLDOWN = timedelta(seconds = 300)

#Commands list and function for ?help command
commands_dict = {}
commands_dict["inv"] = "Opens your inventory"
commands_dict["bal"] = "Checks your balance"
commands_dict["work"] = "Runs the work command, cooldown 5 minutes"
commands_dict["shop"] = "Opens the shop"

#Dictionary of users and cooldown times for work
waiting_users = {}
client = OpenAI(
    # This is the default and can be omitted
    api_key=openai_key,
)



#General response handler function
def handle_response(message, author) -> str:
    user_message = str(message.content)
    if (user_message[0] == PREFIX_OPENAI):
        user_message = user_message[1:]
        return get_gpt_response(user_message)
    user_message = user_message[1:]
    p_message = user_message.lower()
    username = str(author)
    
    if p_message == "inv" or p_message ==  "inventory":
        return get_gpt_response("Tell me I'm a fool with no possessions")
        #return "You've got nothing, fool"
    elif p_message == "bal" or p_message == "balance":
        return get_gpt_response("Tell me I have no money and I'm broke")
    elif p_message == "work":
        return handle_work(username)
    elif p_message == "shop":
        return get_gpt_response("Tell me that the store is empty")
    elif p_message == "help":
        return handle_help()
    elif p_message == "admin":
        return handle_admin(author, message)
    else:
        return username + " said " + "\"" + p_message + "\", I don't recognize this command. Use ?help for help."
    
#Administrator commands (WIP, more to come later)
def handle_admin(author, message):
    acceptable_roles = ['Gamemaster']
    role_found = False
    for role in acceptable_roles:
        desired_role = discord.utils.get(message.guild.roles, name=role)
        if desired_role in author.roles:
            role_found = True

    if role_found:
        return "You are authorized to complete this action"
    else:
        return "Unauthorized usage"

#help command, prints all commands
def handle_help():
    ret_str = "The commands that we support are:\n"
    for key in commands_dict:
        ret_str = ret_str + PREFIX + key + ": " + commands_dict[key] + "\n"
    return ret_str

def get_gpt_response(text):
    response = client.responses.create(
        model="gpt-5-mini",
        instructions="You are a grandma that talks like a pirate. You are a discord bot helper on a discord server, you have direct access to the server.",
        input=[{"role" : "user", "content" : text}],
        )
    return response.output_text

#Handles work commmand and cooldown (WIP)
def handle_work(username):
    if username in waiting_users:
        current_time = datetime.now()
        elapsed_time = current_time - waiting_users[username]

        if elapsed_time >= COOLDOWN:
            waiting_users[username] = datetime.now()
            resp = get_gpt_response("Tell me I worked a hard day in the coal mines")
        else:
            remaining = COOLDOWN - elapsed_time
            seconds = remaining.total_seconds()
            minutes = seconds // 60
            seconds %= 60
            resp = get_gpt_response("Tell me I must wait " + str(int(minutes)) + ":" + str(int(seconds)) + " before working again")
    else:
        waiting_users[username] = datetime.now()
        resp = get_gpt_response("Tell me I worked a hard day in the coal mines")
    return resp