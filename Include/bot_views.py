"""Discord UI view helpers for Splintered Galaxy."""

import discord # pyright: ignore[reportMissingImports]
from typing import List
from Include import shop


def _parse_int(raw: object, label: str) -> int:
    """Strict int parser for modal inputs.

    Accepts an optional leading minus and digits only — `"--5"`, `""`, and
    `"1.0"` all fail. Raises `ValueError` with a user-friendly message.
    """
    text = str(raw).strip()
    candidate = text[1:] if text.startswith("-") else text
    if not candidate or not candidate.isdigit():
        raise ValueError(f"{label} must be an integer")
    return int(text)


class Paginator(discord.ui.View):
    """Two-button (Previous/Next) embed paginator scoped to one user.

    Only the user whose Discord ID matches `author_id` may advance the pages —
    other users get an ephemeral "not yours" reply. The view auto-stops after
    `timeout` seconds of inactivity.
    """

    def __init__(self, pages: List[discord.Embed], author_id: int, timeout: int = 180) -> None:
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current_page = 0
        self.author_id = author_id
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject paging interactions from anyone other than the original requester."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who opened this view can change pages.",
                ephemeral=True,
            )
            return False
        return True

    def _update_buttons(self) -> None:
        """Disable Previous/Next at the page boundaries."""
        self.previous.disabled = self.current_page <= 0
        self.next.disabled = self.current_page >= len(self.pages) - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_page = max(0, self.current_page - 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current_page],
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current_page],
            view=self,
        )


class SaleConfirmationView(discord.ui.View):
    """Accept/Reject prompt for a pending player-to-player item sale.

    Lifecycle:
        1. Created by `bot_responses.handle_sell_item` and sent via
           `channel.send`; the dispatcher then sets `self.message` so
           `on_timeout` can edit the original message.
        2. Only the configured `buyer_id` may interact; other users see an
           ephemeral rejection.
        3. The first accept/reject (or `on_timeout` firing) sets
           `self.completed = True`, after which subsequent clicks are ignored.
    """

    def __init__(
        self,
        seller_id: int,
        buyer_id: int,
        item_name: str,
        quantity: int,
        unit_price: int,
        timeout: int = 300,
    ) -> None:
        super().__init__(timeout=timeout)
        self.seller_id = seller_id
        self.buyer_id = buyer_id
        self.item_name = item_name
        self.quantity = quantity
        self.unit_price = unit_price
        self.total_price = quantity * unit_price
        self.completed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message(
                "Only the receiving player can accept or reject this sale.",
                ephemeral=True,
            )
            return False
        return True

    def _disable_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def _finalize_message(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        color: discord.Color,
    ) -> None:
        self._disable_buttons()
        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="Seller", value=f"<@{self.seller_id}>", inline=True)
        embed.add_field(name="Buyer", value=f"<@{self.buyer_id}>", inline=True)
        embed.add_field(name="Item", value=self.item_name, inline=True)
        embed.add_field(name="Quantity", value=str(self.quantity), inline=True)
        embed.add_field(name="Unit Price", value=f"{self.unit_price} credits", inline=True)
        embed.add_field(name="Total Price", value=f"{self.total_price} credits", inline=True)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        if self.completed:
            return
        self._disable_buttons()
        if getattr(self, "message", None):
            embed = discord.Embed(
                title="Sale Request Expired",
                description=(
                    f"<@{self.buyer_id}> did not respond in time."
                ),
                color=discord.Color.dark_grey(),
            )
            embed.add_field(name="Seller", value=f"<@{self.seller_id}>", inline=True)
            embed.add_field(name="Buyer", value=f"<@{self.buyer_id}>", inline=True)
            embed.add_field(name="Item", value=self.item_name, inline=True)
            embed.add_field(name="Quantity", value=str(self.quantity), inline=True)
            embed.add_field(name="Unit Price", value=f"{self.unit_price} credits", inline=True)
            embed.add_field(name="Total Price", value=f"{self.total_price} credits", inline=True)
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.completed:
            return
        self.completed = True
        try:
            shop.transfer_item(
                seller_id=str(self.seller_id),
                buyer_id=str(self.buyer_id),
                item_name=self.item_name,
                quantity=self.quantity,
                total_price=self.total_price,
            )
            await self._finalize_message(
                interaction,
                "Sale Accepted",
                f"<@{self.buyer_id}> accepted the sale and paid {self.total_price} credits.",
                discord.Color.green(),
            )
        except Exception as e:
            await self._finalize_message(
                interaction,
                "Sale Failed",
                str(e),
                discord.Color.red(),
            )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.completed:
            return
        self.completed = True
        await self._finalize_message(
            interaction,
            "Sale Rejected",
            f"<@{self.buyer_id}> rejected the sale.",
            discord.Color.red(),
        )


class CreateItemModal(discord.ui.Modal):
    def __init__(self, author_id: int, timeout: int = 120):
        super().__init__(title="Create New Shop Item")
        self.author_id = author_id
        self.name = discord.ui.TextInput(label="Item Name", style=discord.TextStyle.short, required=True, max_length=100)
        self.price = discord.ui.TextInput(label="Price (integer)", style=discord.TextStyle.short, required=True, placeholder="e.g. 5000")
        self.quantity = discord.ui.TextInput(label="Quantity (-1 for infinite)", style=discord.TextStyle.short, required=True, placeholder="e.g. 10 or -1")
        self.description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False)
        self.add_item(self.name)
        self.add_item(self.price)
        self.add_item(self.quantity)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            item_name = str(self.name.value).strip()
            if not item_name:
                raise ValueError("Item name cannot be empty")
            price = _parse_int(self.price.value, "Price")
            if price < 0:
                raise ValueError("Price must be non-negative")
            quantity = _parse_int(self.quantity.value, "Quantity")
            if quantity < -1:
                raise ValueError("Quantity must be -1 (infinite) or a non-negative integer")
            description = str(self.description.value).strip()
            shop.add_item(item_name, price, description, quantity)
            await interaction.response.send_message(
                f"Created item {item_name} ({quantity if quantity != -1 else '∞'}) at {price} credits.",
                ephemeral=False,
            )
        except Exception as e:
            await interaction.response.send_message(f"Failed to create item: {e}", ephemeral=True)


class CreateItemView(discord.ui.View):
    def __init__(self, author_id: int, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who started this creation flow can use these controls.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Open Creation Form", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = CreateItemModal(author_id=self.author_id)
        await interaction.response.send_modal(modal)
