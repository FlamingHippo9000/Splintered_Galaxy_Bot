"""Discord bot startup and runtime helpers for Splintered Galaxy."""

import discord  # pyright: ignore[reportMissingImports]
import Include.bot_responses as bot_responses
from Include.env import get_env

# Discord caps a single message payload at 2,000 characters.
MAX_CHARS = 2000


def truncate(text: str) -> str:
    """Trim a string to Discord's per-message character budget."""
    return text[:MAX_CHARS]


async def send_message(message: discord.Message, author: discord.abc.User) -> None:
    """Send a reply message after the bot has generated a response."""
    try:
        response = await bot_responses.handle_response(message, author)
        if isinstance(response, bot_responses.BotResponse):
            content = truncate(response.content) if response.content else None
            sent = await message.channel.send(
                content=content,
                embed=response.embed,
                view=response.view,
            )
            if response.view is not None:
                response.view.message = sent
        else:
            await message.channel.send(truncate(response))
    except Exception as e:
        print("ERROR: send_message exception: " + str(e))


def run_discord_bot() -> None:
    """Create the Discord client, register event handlers, and block on client.run().

    Environment variables are loaded eagerly by `Include.env` on import, so this
    function only needs to read the already-populated `BOT_TOKEN`.
    """
    token = get_env("BOT_TOKEN")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        """Log a startup line and capture the bot's display name for later use."""
        print(f'{client.user} is now running!')
        bot_responses.ADMIN = str(client.user)

    @client.event
    async def on_message(message: discord.Message) -> None:
        """Dispatch messages that begin with a configured command prefix."""
        if message.author == client.user:
            return

        user_message = str(message.content)
        if not user_message:
            return
        if user_message[0] not in (bot_responses.PREFIX, bot_responses.PREFIX_OPENAI):
            return

        channel = str(message.channel)
        print(f"{str(message.author)} said: '{user_message}' in {channel}!")
        await send_message(message, message.author)

    client.run(token)

