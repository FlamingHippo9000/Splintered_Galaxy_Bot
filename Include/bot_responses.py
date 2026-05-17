"""Discord bot command parsing and response handling for Splintered Galaxy."""

import inspect
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import discord  # pyright: ignore[reportMissingImports]
from openai import OpenAI  # pyright: ignore[reportMissingImports]

import Include.cross_bot_calls as boat_calls
from Include import bot_views, shop
from Include.env import get_env

openai_key = get_env("OPENAI_KEY")

# Bot command prefixes and configuration
PREFIX = "?"
PREFIX_OPENAI = "~"
GUILD_ID = get_env("GUILD_ID")
OPENAI_FLAG = False

EDIT_COMMAND_STRUCTURE = "Invalid Command, usage is: " + PREFIX + "edit_item <Item Name (Exact)> <Field> <New Value>"
GET_COMMAND_STRUCTURE = "Invalid Command, usage is: " + PREFIX + "get_item <Item Name (Exact)>"  

#Ignore for now probably can delete later
ADMIN = ''

#Work Cooldown
COOLDOWN = timedelta(minutes=30)

# Tracks per-player cooldown timestamps for the ?work command.
waiting_users: Dict[str, datetime] = {}
client = OpenAI(
    # This is the default and can be omitted
    api_key=openai_key,
)

ITEMS_PER_PAGE = 6

@dataclass
class BotResponse:
    content: Optional[str] = None
    embed: Optional[discord.Embed] = None
    view: Optional[discord.ui.View] = None


def _build_embed(title: str, description: str, color: discord.Color = discord.Color.blurple()) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    return embed


def _build_simple_response(title: str, description: str, color: discord.Color = discord.Color.blurple()) -> BotResponse:
    return BotResponse(embed=_build_embed(title, description, color))


def _build_error_response(message: str) -> BotResponse:
    return _build_simple_response("Error", message, discord.Color.red())


def _build_success_response(message: str) -> BotResponse:
    return _build_simple_response("Success", message, discord.Color.green())





def _paginate(items: List[Dict[str, object]], page_size: int) -> List[List[Dict[str, object]]]:
    return [items[i : i + page_size] for i in range(0, len(items), page_size)]


def _build_embed_pages(
    title: str,
    rows: List[Dict[str, object]],
    description_func: Callable[[Dict[str, object]], str],
    page_size: int = ITEMS_PER_PAGE,
) -> List[discord.Embed]:
    """Slice `rows` into Discord embed pages of at most `page_size` fields each.

    `description_func` formats a single row's body text.
    """
    pages: List[discord.Embed] = []
    total_pages = max(1, len(rows) // page_size + (1 if len(rows) % page_size else 0))
    for page_index, page_items in enumerate(_paginate(rows, page_size)):
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        for row in page_items:
            embed.add_field(
                name=row["name"],
                value=description_func(row),
                inline=False,
            )
        embed.set_footer(text=f"Page {page_index + 1}/{total_pages}")
        pages.append(embed)
    return pages


def _format_shop_description(item: Dict[str, object]) -> str:
    quantity = "∞" if item.get("quantity") == -1 else item.get("quantity")
    return (
        f"{item.get('description', 'No description available')}\n"
        f"**Price:** {item.get('price')} credits\n"
        f"**Stock:** {quantity}"
    )


def _format_item_description(item: Dict[str, object]) -> str:
    return (
        f"{item.get('description', 'No description available')}\n"
        f"**Price:** {item.get('price')} credits"
    )


def _format_inventory_description(item: Dict[str, object]) -> str:
    return f"Quantity: {item.get('quantity')}"


@dataclass(frozen=True)
class Command:
    """Metadata for a single user-facing chat command.

    Attributes:
        name: Canonical name shown in `?help`. Always matched case-insensitively.
        description: Human-readable summary surfaced in `?help`.
        handler: Callable invoked when the command fires. May be sync or async;
            the dispatcher awaits the result when needed.
        aliases: Alternate spellings users may type (also lowercased on match).
        require_ssm: When True, the caller must hold the "Senior System Manager"
            role and be invoking from a guild channel.
        needs_author: Pass the message's author to `handler`.
        needs_message: Pass the raw `discord.Message` to `handler` (used by
            handlers that themselves need `message.guild`).
        needs_split: Pass the shlex-split argument vector to `handler`.
    """

    name: str
    description: str
    handler: Callable[..., Union[BotResponse, Awaitable[BotResponse]]]
    aliases: tuple = ()
    require_ssm: bool = False
    needs_author: bool = False
    needs_message: bool = False
    needs_split: bool = False


# Registry of every chat command. `?help` and dispatch share this single source
# of truth, so adding a command requires touching exactly one place.
COMMANDS: List[Command] = [
    Command("inv", "Opens your inventory",
            lambda author: handle_inventory(author),
            aliases=("inventory",), needs_author=True),
    Command("bal", "Checks your balance",
            lambda author: handle_balance(author),
            aliases=("balance",), needs_author=True),
    Command("items", "Shows all available items",
            lambda author: handle_items(author),
            needs_author=True),
    Command("shop", "Shows current shop stock",
            lambda author: handle_shop(author),
            needs_author=True),
    Command("buy", "Purchase an item from the shop",
            lambda author, split: handle_buy(author, split),
            needs_author=True, needs_split=True),
    Command("shop_add", "Add a new item to the shop (Senior System Manager only)",
            lambda split: handle_shop_add(split),
            aliases=("create_item", "create-item", "add_item"),
            require_ssm=True, needs_split=True),
    Command("create_item_interactive", "Interactive item creation (Senior System Manager only)",
            lambda author, message: handle_create_item_interactive(author, message),
            aliases=("create-item-interactive", "create-item-modal"),
            require_ssm=True, needs_author=True, needs_message=True),
    Command("shop_stock", "Update shop stock for an item (Senior System Manager only)",
            lambda split: handle_shop_stock(split),
            require_ssm=True, needs_split=True),
    Command("shop_edit", "Edit an item price or description (Senior System Manager only)",
            lambda split: handle_shop_edit(split),
            require_ssm=True, needs_split=True),
    Command("bal_set", "Set a player's balance exactly (Senior System Manager only)",
            lambda split: handle_balance_set(split),
            require_ssm=True, needs_split=True),
    Command("bal_add", "Add a delta to a player's balance (Senior System Manager only)",
            lambda split: handle_balance_add(split),
            require_ssm=True, needs_split=True),
    Command("bal_remove", "Subtract a delta from a player's balance (Senior System Manager only)",
            lambda split: handle_balance_remove(split),
            require_ssm=True, needs_split=True),
    Command("give_item", "Grant an item to a player (Senior System Manager only)",
            lambda split: handle_give_item(split),
            aliases=("give-item",), require_ssm=True, needs_split=True),
    Command("remove_item", "Remove an item from a player (Senior System Manager only)",
            lambda split: handle_remove_item(split),
            aliases=("remove-item",), require_ssm=True, needs_split=True),
    Command("use_item", "Use an item from your inventory",
            lambda author, split: handle_use_item(author, split),
            aliases=("use-item", "use"), needs_author=True, needs_split=True),
    Command("drop_item", "Remove an item from your inventory",
            lambda author, split: handle_drop_item(author, split),
            aliases=("drop-item", "drop"), needs_author=True, needs_split=True),
    Command("sell_item", "Sell an item to another player",
            lambda author, split: handle_sell_item(author, split),
            aliases=("sell-item", "sell"), needs_author=True, needs_split=True),
    Command("item_info", "Show price, description, and shop stock for an item",
            lambda split: handle_item_info(split),
            needs_split=True),
    Command("currency_icon", "Set or clear the currency icon URL (Senior System Manager only)",
            lambda split: handle_currency_icon(split),
            require_ssm=True, needs_split=True),
    Command("work", "Work for 5,000,000 credits every 30 minutes",
            lambda author: handle_work(author),
            needs_author=True),
    Command("help", "Show this help message",
            lambda: handle_help()),
    Command("admin", "Check admin authorization",
            lambda author, message: handle_admin(author, message),
            needs_author=True, needs_message=True),
    Command("edit_item", "Edit a boat item field (Senior System Manager only)",
            lambda split: handle_edit(split),
            aliases=("e_item", "edit-item"), require_ssm=True, needs_split=True),
    Command("get_item", "Look up a boat item ID by name (Senior System Manager only)",
            lambda split: handle_get(split),
            aliases=("g_item", "get-item"), require_ssm=True, needs_split=True),
]


def _build_command_lookup(commands: List[Command]) -> Dict[str, Command]:
    """Return a {alias -> Command} map. Every name and alias is lowercased."""
    lookup: Dict[str, Command] = {}
    for cmd in commands:
        for key in (cmd.name, *cmd.aliases):
            lookup[key.lower()] = cmd
    return lookup


COMMAND_LOOKUP: Dict[str, Command] = _build_command_lookup(COMMANDS)


def _build_handler_kwargs(cmd: Command, author, message, split_message: List[str]) -> Dict[str, Any]:
    """Pick the subset of dispatch context the command actually wants."""
    kwargs: Dict[str, Any] = {}
    if cmd.needs_author:
        kwargs["author"] = author
    if cmd.needs_message:
        kwargs["message"] = message
    if cmd.needs_split:
        kwargs["split"] = split_message
    return kwargs


async def handle_response(message: discord.Message, author: discord.abc.User) -> Any:
    """Parse `message.content` and dispatch to the matching command handler.

    Returns either a `BotResponse` (the standard envelope) or a plain string
    response from the OpenAI fallback. Returning a structured error envelope
    is always preferred over raising — the caller doesn't try/except.
    """
    user_message = str(message.content)
    if not user_message:
        return _build_error_response("No command specified. Use ?help for help.")

    # `~`-prefixed messages are the OpenAI fallback (feature-flagged off by default).
    if user_message[0] == PREFIX_OPENAI:
        if not OPENAI_FLAG:
            return _build_error_response("OpenAI integration is currently disabled.")
        return get_gpt_response(user_message[1:])

    # Strip the `?` prefix, then shlex-split so quoted item names survive.
    user_message = user_message[1:]
    try:
        split_message = shlex.split(user_message)
    except ValueError as e:
        print("WARN: handle_response() exception: " + str(e))
        return _build_error_response("Mismatched quotes, please recheck command")
    if not split_message:
        return _build_error_response("No command specified. Use ?help for help.")

    cmd = COMMAND_LOOKUP.get(split_message[0].lower())
    if cmd is None:
        return _build_error_response(
            f"{author} said \"{user_message}\", I don't recognize this command. Use ?help for help."
        )

    if cmd.require_ssm and not handle_senior_sys_manager(author, message):
        return _build_error_response("You are not authorized to complete this action")

    result = cmd.handler(**_build_handler_kwargs(cmd, author, message, split_message))
    if inspect.isawaitable(result):
        result = await result
    return result


def is_int(val: str) -> bool:
    """Return True if a string value can be parsed as an integer."""
    try:
        int(val)
        return True
    except ValueError:
        return False

async def handle_get(split_message: List[str]) -> BotResponse:
    """Resolve an Unbelievaboat item ID by name (Senior System Manager only)."""
    if len(split_message) != 2:
        return _build_error_response(GET_COMMAND_STRUCTURE)

    item_id = await get_item_id(split_message[1])
    item_id = int(item_id)
    if item_id == boat_calls.BAD_RESPONSE.TOO_MANY_ITEMS.value:
        return _build_error_response("Invalid item name, too many items - please use dashboard")
    if item_id == boat_calls.BAD_RESPONSE.INVALID_ITEM.value:
        return _build_error_response("Invalid Item Name, please check item name")
    if item_id == boat_calls.BAD_RESPONSE.RATE_LIMIT.value:
        return _build_error_response("Too many Unbelievaboat calls (rate limiting), retry command")

    return _build_success_response(f"Item ID {item_id}")

async def get_item_id(name: str) -> int:
    """Query the boat API for an item ID by name."""
    return await boat_calls.handle_query_item(GUILD_ID, name)

async def handle_edit(split_message: List[str]) -> BotResponse:
    """Update an Unbelievaboat item field (Senior System Manager only)."""
    if len(split_message) != 4:
        return _build_error_response(EDIT_COMMAND_STRUCTURE)

    item_name = split_message[1]
    item_id = await get_item_id(item_name)
    item_id = int(item_id)
    if item_id == boat_calls.BAD_RESPONSE.TOO_MANY_ITEMS.value:
        return _build_error_response("Invalid item name, too many items - please use dashboard")
    if item_id == boat_calls.BAD_RESPONSE.INVALID_ITEM.value:
        return _build_error_response("Invalid Item Name, please check item name")
    if item_id == boat_calls.BAD_RESPONSE.RATE_LIMIT.value:
        return _build_error_response("Too many Unbelievaboat calls (rate limiting), retry command")

    field = split_message[2]
    value = split_message[3]
    response = await boat_calls.update_item(GUILD_ID, str(item_id), field, value)
    if is_int(str(response)) and int(response) == boat_calls.BAD_RESPONSE.RATE_LIMIT.value:
        return _build_error_response("Boat update failed (rate limit or API error), retry command")
    if response == "":
        return _build_error_response(f"Unsupported field: {field}")
    return _build_success_response(
        f"Updated: \"{item_name}\", Field: \"{field}\" to \"{response}\""
    )

SENIOR_SYS_MANAGER_ROLE = "Senior System Manager"


def handle_senior_sys_manager(author: discord.abc.User, message: discord.Message) -> bool:
    """Return True iff `author` holds the Senior System Manager role in this guild.

    Returns False (rather than raising) for DMs and webhook authors, which is
    the behavior every caller in the dispatcher expects.
    """
    if message.guild is None or not hasattr(author, "roles"):
        return False
    role = discord.utils.get(message.guild.roles, name=SENIOR_SYS_MANAGER_ROLE)
    if role is None:
        return False
    return role in author.roles


def _parse_quantity_or_inf(arg: str) -> Optional[int]:
    """Parse a stock-quantity argument.

    Returns `-1` for the "inf"/"infinite" sentinels (matching the shop layer's
    convention for unlimited stock), an int for valid integers, or `None`
    when the input cannot be interpreted as either.
    """
    if arg.lower() in ("inf", "infinite"):
        return -1
    if is_int(arg):
        return int(arg)
    return None


def _parse_optional_quantity(args: List[str], idx: int, default: int = 1) -> Union[int, BotResponse]:
    """Parse `args[idx]` as a quantity, returning `default` when the arg is absent.

    On invalid input returns a `BotResponse` error envelope so callers can
    propagate it without duplicating the validation message.
    """
    if idx >= len(args):
        return default
    if not is_int(args[idx]):
        return _build_error_response("Quantity must be an integer.")
    return int(args[idx])


def handle_items(author: discord.abc.User) -> BotResponse:
    """Return a paginated catalog embed for all items available in the database."""
    items = shop.get_items()
    if not items:
        return _build_error_response("No items are available in the catalog yet.")
    pages = _build_embed_pages("Available Items", items, _format_item_description)
    view = bot_views.Paginator(pages, author.id)
    return BotResponse(embed=pages[0], view=view)


def handle_shop(author: discord.abc.User) -> BotResponse:
    """Return a paginated shop embed for current stock quantities and prices."""
    shop_list = shop.get_shop()
    if not shop_list:
        return _build_error_response("The shop is currently empty.")

    pages = _build_embed_pages("Shop Stock", shop_list, _format_shop_description)
    view = bot_views.Paginator(pages, author.id)
    return BotResponse(embed=pages[0], view=view)


def handle_inventory(author: discord.abc.User) -> BotResponse:
    """Return a formatted inventory list for the current user."""
    player_id = str(author.id)
    shop.ensure_player(player_id)
    inventory = shop.get_inventory(player_id)
    if not inventory:
        return _build_error_response("Your inventory is empty. Use ?shop to browse items.")
    rows = [{"name": name, "quantity": quantity} for name, quantity in inventory.items()]
    pages = _build_embed_pages(
        "Your Inventory",
        rows,
        lambda item: _format_inventory_description(item),
    )
    view = bot_views.Paginator(pages, author.id)
    return BotResponse(embed=pages[0], view=view)


def handle_balance(author: discord.abc.User) -> BotResponse:
    """Return the current balance for the requesting user."""
    player_id = str(author.id)
    shop.ensure_player(player_id)
    balance = shop.get_balance(player_id)
    icon_url = shop.get_currency_icon()
    embed = discord.Embed(
        title="Current Balance",
        description=f"**{balance} credits**",
        color=discord.Color.green(),
    )
    if icon_url:
        embed.set_thumbnail(url=icon_url)
    return BotResponse(embed=embed)


async def handle_buy(author: discord.abc.User, split_message: List[str]) -> BotResponse:
    """Purchase an item from the shop for the current user."""
    if len(split_message) < 2:
        return _build_error_response("Usage: ?buy <item_name> [quantity]")
    item_name = split_message[1]
    quantity = 1
    if len(split_message) > 2:
        if not is_int(split_message[2]):
            return _build_error_response("Quantity must be an integer.")
        quantity = int(split_message[2])

    player_id = str(author.id)
    shop.ensure_player(player_id)
    try:
        result = shop.buy_item(player_id, item_name, quantity)
        return _build_success_response(
            f"Purchased {result['quantity']} x {result['item_name']} for {result['total_cost']} credits."
        )
    except Exception as e:
        return _build_error_response(str(e))


def handle_shop_add(split_message: List[str]) -> BotResponse:
    """Add a new catalog item and seed its shop stock."""
    if len(split_message) < 4:
        return _build_error_response("Usage: ?shop_add <name> <price> <quantity> [description]")
    item_name = split_message[1]
    if not is_int(split_message[2]):
        return _build_error_response("Price must be an integer.")
    price = int(split_message[2])
    quantity = _parse_quantity_or_inf(split_message[3])
    if quantity is None:
        return _build_error_response("Quantity must be an integer or 'inf'.")
    description = " ".join(split_message[4:]) if len(split_message) > 4 else ""
    try:
        shop.add_item(item_name, price, description, quantity)
        quantity_label = "∞" if quantity == -1 else str(quantity)
        return _build_success_response(
            f"Added {quantity_label} x {item_name} at {price} credits each to the shop."
        )
    except Exception as e:
        return _build_error_response(str(e))


def handle_create_item_interactive(author: discord.abc.User, message: discord.Message) -> BotResponse:
    """Return a view that opens an interactive modal for creating a shop item.

    The dispatcher enforces Senior System Manager authorization for this
    command via `require_ssm=True` on the Command entry.
    """
    view = bot_views.CreateItemView(author.id)
    embed = discord.Embed(
        title="Interactive Item Creation",
        description="Click the button below to open the item creation form.",
        color=discord.Color.blue(),
    )
    return BotResponse(embed=embed, view=view)


def handle_currency_icon(split_message: List[str]) -> BotResponse:
    """Set or clear the currency icon URL for balance embeds."""
    if len(split_message) != 2:
        return _build_error_response("Usage: ?currency_icon <url|clear>")

    target = split_message[1].strip()
    if target.lower() == "clear":
        shop.clear_currency_icon()
        return _build_success_response("Cleared the custom currency icon.")

    if not (target.startswith("http://") or target.startswith("https://")):
        return _build_error_response(
            "Currency icon must be a publicly accessible URL starting with http:// or https://."
        )
    if not target.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return _build_error_response(
            "Currency icon must point to a PNG, JPG, JPEG, or WEBP image."
        )

    shop.set_currency_icon(target)
    return _build_success_response(f"Currency icon set to {target}.")


def handle_shop_stock(split_message: List[str]) -> BotResponse:
    """Overwrite the shop stock quantity for an existing item."""
    if len(split_message) != 3:
        return _build_error_response("Usage: ?shop_stock <name> <quantity>")
    item_name = split_message[1]
    quantity = _parse_quantity_or_inf(split_message[2])
    if quantity is None:
        return _build_error_response("Quantity must be an integer or 'inf'.")
    try:
        shop.set_shop_stock(item_name, quantity)
        quantity_label = "∞" if quantity == -1 else str(quantity)
        return _build_success_response(f"Updated shop stock for {item_name} to {quantity_label}.")
    except Exception as e:
        return _build_error_response(str(e))


def handle_shop_edit(split_message: List[str]) -> BotResponse:
    """Edit an item's price or description (Senior System Manager only)."""
    if len(split_message) < 4:
        return _build_error_response("Usage: ?shop_edit <name> <price|description> <value>")
    item_name = split_message[1]
    field = split_message[2].lower()
    value = " ".join(split_message[3:])
    try:
        if field == "price":
            if not is_int(value):
                return _build_error_response("Price must be an integer.")
            shop.update_item(item_name, price=int(value))
            return _build_success_response(f"Set {item_name} price to {value} credits.")
        if field == "description":
            shop.update_item(item_name, description=value)
            return _build_success_response(f"Updated {item_name} description.")
        return _build_error_response("Supported fields: price, description.")
    except Exception as e:
        return _build_error_response(str(e))


def _resolve_player_id(player_identifier: str) -> str:
    """Convert a Discord mention or raw ID into a player ID string."""
    identifier = player_identifier.strip()
    if identifier.startswith("<@") and identifier.endswith(">"):
        identifier = identifier[2:-1]
        if identifier.startswith("!"):
            identifier = identifier[1:]
    return identifier


def _balance_op(
    split_message: List[str],
    usage: str,
    value_label: str,
    apply: Callable[[str, int], int],
    success_template: str,
) -> BotResponse:
    """Shared validation+execution for `?bal_set`, `?bal_add`, and `?bal_remove`.

    `apply` performs the actual shop mutation and returns the new balance.
    `success_template` is a format string given `{amount}`, `{player_id}`,
    and `{balance}`.
    """
    if len(split_message) != 3:
        return _build_error_response(usage)
    player_id = _resolve_player_id(split_message[1])
    if not is_int(split_message[2]):
        return _build_error_response(f"{value_label} must be an integer.")
    amount = int(split_message[2])
    try:
        balance = apply(player_id, amount)
        return _build_success_response(
            success_template.format(amount=amount, player_id=player_id, balance=balance)
        )
    except Exception as e:
        return _build_error_response(str(e))


def handle_balance_set(split_message: List[str]) -> BotResponse:
    """Set a player's balance to an exact amount."""
    return _balance_op(
        split_message,
        usage="Usage: ?bal_set <player_id|@user> <amount>",
        value_label="Amount",
        apply=lambda pid, amt: shop.set_balance(pid, amt),
        success_template="Set balance for <@{player_id}> to {balance} credits.",
    )


def handle_balance_add(split_message: List[str]) -> BotResponse:
    """Add a delta to a player's balance."""
    return _balance_op(
        split_message,
        usage="Usage: ?bal_add <player_id|@user> <delta>",
        value_label="Delta",
        apply=lambda pid, amt: shop.change_balance(pid, amt),
        success_template="Added {amount} credits to <@{player_id}>. New balance: {balance} credits.",
    )


def handle_balance_remove(split_message: List[str]) -> BotResponse:
    """Subtract a delta from a player's balance."""
    return _balance_op(
        split_message,
        usage="Usage: ?bal_remove <player_id|@user> <delta>",
        value_label="Delta",
        apply=lambda pid, amt: shop.change_balance(pid, -amt),
        success_template="Removed {amount} credits from <@{player_id}>. New balance: {balance} credits.",
    )


def _admin_inventory_op(
    split_message: List[str],
    usage: str,
    apply: Callable[[str, str, int], None],
    success_template: str,
) -> BotResponse:
    """Shared body for `?give_item` and `?remove_item` (admin-targeted inventory edits).

    `apply(player_id, item_name, quantity)` performs the shop mutation;
    `success_template` is a format string taking `{quantity}`, `{item_name}`,
    and `{player_id}`.
    """
    if len(split_message) < 3:
        return _build_error_response(usage)
    player_id = _resolve_player_id(split_message[1])
    item_name = split_message[2]
    quantity = _parse_optional_quantity(split_message, idx=3)
    if isinstance(quantity, BotResponse):
        return quantity
    try:
        shop.ensure_player(player_id)
        apply(player_id, item_name, quantity)
        return _build_success_response(
            success_template.format(quantity=quantity, item_name=item_name, player_id=player_id)
        )
    except Exception as e:
        return _build_error_response(str(e))


def _self_inventory_remove(
    author: discord.abc.User,
    split_message: List[str],
    usage: str,
    verb: str,
) -> BotResponse:
    """Shared body for `?use_item` and `?drop_item` (self-targeted removal).

    `verb` is the past-tense action ("Used", "Dropped") shown in the success message.
    """
    if len(split_message) < 2:
        return _build_error_response(usage)
    player_id = str(author.id)
    item_name = split_message[1]
    quantity = _parse_optional_quantity(split_message, idx=2)
    if isinstance(quantity, BotResponse):
        return quantity
    try:
        shop.ensure_player(player_id)
        shop.remove_inventory_item(player_id, item_name, quantity)
        return _build_success_response(f"{verb} {quantity} x {item_name}.")
    except Exception as e:
        return _build_error_response(str(e))


def handle_give_item(split_message: List[str]) -> BotResponse:
    """Grant an item directly to a player's inventory (SSM only)."""
    return _admin_inventory_op(
        split_message,
        usage="Usage: ?give_item <player_id|@user> <item_name> [quantity]",
        apply=shop.add_inventory_item,
        success_template="Granted {quantity} x {item_name} to <@{player_id}>.",
    )


def handle_remove_item(split_message: List[str]) -> BotResponse:
    """Remove an item directly from a player's inventory (SSM only)."""
    return _admin_inventory_op(
        split_message,
        usage="Usage: ?remove_item <player_id|@user> <item_name> [quantity]",
        apply=shop.remove_inventory_item,
        success_template="Removed {quantity} x {item_name} from <@{player_id}>.",
    )


def handle_use_item(author: discord.abc.User, split_message: List[str]) -> BotResponse:
    """Consume an item from the invoking player's inventory."""
    return _self_inventory_remove(
        author, split_message,
        usage="Usage: ?use_item <item_name> [quantity]",
        verb="Used",
    )


def handle_drop_item(author: discord.abc.User, split_message: List[str]) -> BotResponse:
    """Discard an item from the invoking player's inventory."""
    return _self_inventory_remove(
        author, split_message,
        usage="Usage: ?drop_item <item_name> [quantity]",
        verb="Dropped",
    )


async def handle_sell_item(author: discord.abc.User, split_message: List[str]) -> BotResponse:
    """Request a player-to-player item sale with a buyer confirmation prompt."""
    if len(split_message) < 4:
        return _build_error_response("Usage: ?sell_item <buyer_id|@user> <item_name> <price> [quantity]")
    seller_id = str(author.id)
    buyer_id = _resolve_player_id(split_message[1])
    if buyer_id == seller_id:
        return _build_error_response("You cannot sell items to yourself.")
    item_name = split_message[2]
    if not is_int(split_message[3]):
        return _build_error_response("Price must be an integer.")
    unit_price = int(split_message[3])
    quantity = 1
    if len(split_message) > 4:
        if not is_int(split_message[4]):
            return _build_error_response("Quantity must be an integer.")
        quantity = int(split_message[4])
    try:
        shop.ensure_player(seller_id)
        inventory = shop.get_inventory(seller_id)
        current_qty = inventory.get(item_name, 0)
        if current_qty < quantity:
            return _build_error_response("You do not have enough of that item to sell.")
        view = bot_views.SaleConfirmationView(
            seller_id=int(seller_id),
            buyer_id=int(buyer_id),
            item_name=item_name,
            quantity=quantity,
            unit_price=unit_price,
        )
        total_price = quantity * unit_price
        embed = discord.Embed(
            title="Item Sale Request",
            description=(
                f"<@{seller_id}> wants to sell {quantity} x {item_name} to <@{buyer_id}> for {total_price} credits."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Seller", value=f"<@{seller_id}>", inline=True)
        embed.add_field(name="Buyer", value=f"<@{buyer_id}>", inline=True)
        embed.add_field(name="Item", value=item_name, inline=True)
        embed.add_field(name="Quantity", value=str(quantity), inline=True)
        embed.add_field(name="Unit Price", value=f"{unit_price} credits", inline=True)
        embed.add_field(name="Total Price", value=f"{total_price} credits", inline=True)
        embed.set_footer(text="Buyer has 5 minutes to accept or reject this sale.")
        return BotResponse(embed=embed, view=view)
    except Exception as e:
        return _build_error_response(str(e))


def handle_item_info(split_message: List[str]) -> BotResponse:
    """Return the catalog description, price, and shop stock for a named item."""
    if len(split_message) != 2:
        return _build_error_response("Usage: ?item_info <item_name>")

    item_name = split_message[1]
    item_row = shop.get_item_by_name(item_name)
    if not item_row:
        return _build_error_response(f"Item '{item_name}' not found.")

    shop_item = next((item for item in shop.get_shop() if item["name"] == item_name), None)
    quantity = shop_item["quantity"] if shop_item is not None else 0
    quantity_label = "∞" if quantity == -1 else str(quantity)

    embed = discord.Embed(
        title=f"{item_row['name']} Information",
        description="Item details are shown below.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Description", value=item_row["description"] or "No description available.", inline=False)
    embed.add_field(name="Price:", value=f"{item_row['price']} credits", inline=False)
    embed.add_field(name="Shop Stock:", value=quantity_label, inline=False)
    return BotResponse(embed=embed)


# WIP: only Gamemaster is whitelisted today; broaden this list as roles are added.
ADMIN_ROLES = ("Gamemaster",)


def handle_admin(author: discord.abc.User, message: discord.Message) -> BotResponse:
    """Stub that confirms whether `author` holds any of `ADMIN_ROLES`."""
    if message.guild is None or not hasattr(author, "roles"):
        return _build_error_response("Unauthorized usage")
    for role_name in ADMIN_ROLES:
        role = discord.utils.get(message.guild.roles, name=role_name)
        if role is not None and role in author.roles:
            return _build_success_response("You are authorized to complete this action")
    return _build_error_response("Unauthorized usage")

def handle_help() -> BotResponse:
    """Render the `?help` embed by walking the canonical command registry."""
    embed = discord.Embed(
        title="Splintered Galaxy Help",
        description="Browse available commands below.",
        color=discord.Color.blue(),
    )
    command_lines = [f"**{PREFIX}{cmd.name}** — {cmd.description}" for cmd in COMMANDS]
    embed.add_field(
        name="Commands",
        value="\n".join(command_lines),
        inline=False,
    )
    embed.set_footer(text="Use ?help again anytime to refresh this menu.")
    return BotResponse(embed=embed)


def get_gpt_response(text: str) -> str:
    """Forward `text` to the OpenAI Responses API and return the raw output text."""
    response = client.responses.create(
        model="gpt-5-mini",
        instructions=(
            "You are a grandma that talks like a pirate. You are a discord bot helper "
            "on a discord server, you have direct access to the server."
        ),
        input=[{"role": "user", "content": text}],
    )
    return response.output_text


WORK_REWARD = 5_000_000


def handle_work(author: discord.abc.User) -> BotResponse:
    """Grant the work reward to `author` if they're off cooldown.

    The first invocation per player always succeeds and starts a fresh cooldown
    timer. Subsequent invocations within `COOLDOWN` return a friendly
    mm:ss-remaining error.
    """
    player_id = str(author.id)
    shop.ensure_player(player_id)
    now = datetime.now()

    last_worked = waiting_users.get(player_id)
    if last_worked is not None:
        elapsed = now - last_worked
        if elapsed < COOLDOWN:
            remaining_seconds = int((COOLDOWN - elapsed).total_seconds())
            minutes, seconds = divmod(remaining_seconds, 60)
            return _build_error_response(
                f"You must wait {minutes}:{seconds:02d} before working again."
            )

    waiting_users[player_id] = now
    updated_balance = shop.change_balance(player_id, WORK_REWARD)
    return _build_success_response(
        f"Work complete! You earned {WORK_REWARD:,} credits.\n"
        f"New balance: {updated_balance} credits."
    )