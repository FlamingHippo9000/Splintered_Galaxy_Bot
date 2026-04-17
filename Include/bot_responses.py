import discord

#For cooldown and timekeeping
from datetime import datetime
from datetime import timedelta
from openai import OpenAI
import os
import shlex
import Include.cross_bot_calls as boat_calls

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
PREFIX = "?"
PREFIX_OPENAI = "~"
GUILD_ID = get_env("GUILD_ID")

EDIT_COMMAND_STRUCTURE = "Invalid Command, usage is: " + PREFIX + "edit_item <Item Name (Exact)> <Field> <New Value>"
GET_COMMAND_STRUCTURE = "Invalid Command, usage is: " + PREFIX + "get_item <Item Name (Exact)>"  

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
async def handle_response(message, author) -> str:
    user_message = str(message.content)
    # if (user_message[0] == PREFIX_OPENAI):
    #     user_message = user_message[1:]
    #     return get_gpt_response(user_message)
    user_message = user_message[1:]
    #p_message = user_message.lower()
    username = str(author)
    invalid_command_msg = username + " said " + "\"" + user_message + "\", I don't recognize this command. Use ?help for help."
    split_message = []
    try:
        split_message = shlex.split(user_message)
    except Exception as e:
        print("WARN: handle_response() exception: " + str(e))
        return "Mismatched quotes, please recheck command"
    if len(split_message) == 0:
         return "No command specified. Use ?help for help."
    first = split_message[0]
    
    # Disabled for now, will add later
    # if first == "inv" or first ==  "inventory":
    #     return get_gpt_response("Tell me I'm a fool with no possessions")
    # elif first == "bal" or first == "balance":
    #     return get_gpt_response("Tell me I have no money and I'm broke")
    # elif first == "work":
    #     return handle_work(username)
    # elif first == "shop":
    #     return get_gpt_response("Tell me that the store is empty")
    # elif first == "help":
    #     return handle_help()
    # elif first == "admin":
    #     return handle_admin(author, message)
    if first == "edit_item" or first ==  "e_item" or first == "edit-item" :
        if handle_senior_sys_manager(author, message):
            return await handle_edit(split_message)
        else:
            return "You are not authorized to complete this action"
    elif first == "get_item" or first == "g_item" or first == "get-item":
        if handle_senior_sys_manager(author, message):
            return await handle_get(split_message)
        else:
            return "You are not authorized to complete this action"
    else:
        return invalid_command_msg
    
def is_int(val):
    try:
        int(val)
        return True
    except ValueError:
        return False
    
async def handle_get(split_message):
    if len(split_message) != 2:
        return EDIT_COMMAND_STRUCTURE
    
    item_id = await get_item_id(split_message[1])
    item_id = int(item_id)
    if item_id == boat_calls.BAD_RESPONSE.TOO_MANY_ITEMS:
        return "Invalid item name, too many items - please use dashboard"
    if item_id == boat_calls.BAD_RESPONSE.INVALID_ITEM:
        return "Invalid Item Name, please check item name"
    if item_id == boat_calls.BAD_RESPONSE.RATE_LIMIT:
        return "Too many Unbelievaboat calls (rate limiting), retry command"
        
    return "Item ID " + str(item_id)

async def get_item_id(name):
    return await boat_calls.handle_query_item(GUILD_ID, name)

async def handle_edit(split_message):
    if len(split_message) != 4:
        return EDIT_COMMAND_STRUCTURE
    
    item_name = split_message[1]
    item_id = await get_item_id(item_name)
    item_id = int(item_id)
    if item_id == boat_calls.BAD_RESPONSE.TOO_MANY_ITEMS.value:
        return "Invalid item name, too many items - please use dashboard"
    if item_id == boat_calls.BAD_RESPONSE.INVALID_ITEM.value:
        return "Invalid Item Name, please check item name"
    if item_id == boat_calls.BAD_RESPONSE.RATE_LIMIT.value:
        return "Too many Unbelievaboat calls (rate limiting), retry command"
    field = split_message[2]
    value = split_message[3]
    response = await boat_calls.update_item(GUILD_ID, str(item_id), field, value)    
    return "Updated \"" + item_name + "\", Field: " + field + " to " + response

def handle_senior_sys_manager(author, message):
    acceptable_role = "Senior System Manager"
    role_found = False
    desired_role = discord.utils.get(message.guild.roles, name=acceptable_role)
    if desired_role in author.roles:
        role_found = True

    return role_found
    
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