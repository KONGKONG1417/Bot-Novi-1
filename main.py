import discord
from discord.ext import commands
import datetime
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

auctions = {}

class BidModal(discord.ui.Modal):
    def __init__(self, item_message_id):
        super().__init__(title="💰 ใส่ราคาประมูล")
        self.item_message_id = item_message_id
        self.price_input = discord.ui.TextInput(label="ราคาประมูล (บาท)", required=True)
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction):
        auction = auctions[self.item_message_id]
        bid_price = int(self.price_input.value)
        if bid_price > auction["price"]:
            auction["price"] = bid_price
            auction["user"] = interaction.user
            embed = discord.Embed(
                title=f"🏆 ประมูล: {auction['item_name']}",
                description=f"💰 {auction['price']} บาท\n👑 {auction['user'].mention}",
                color=0xFFD700,
            )
            await auction["message"].edit(embed=embed, view=BidButton(self.item_message_id))
            await interaction.response.send_message("✅ ประมูลสำเร็จ!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ ราคาต้องสูงกว่าปัจจุบัน!", ephemeral=True)

class BidButton(discord.ui.View):
    def __init__(self, item_message_id):
        super().__init__(timeout=None)
        self.item_message_id = item_message_id

    @discord.ui.button(label="💰 ประมูล", style=discord.ButtonStyle.green)
    async def bid(self, _, interaction):
        await interaction.response.send_modal(BidModal(self.item_message_id))

@bot.command()
async def create(ctx, item_name: str, starting_price: int):
    embed = discord.Embed(
        title=f"🏆 ประมูล: {item_name}",
        description=f"💰 เริ่มที่ {starting_price} บาท\n👑 ยังไม่มีผู้ประมูล",
        color=0x00FF00,
    )
    msg = await ctx.send(embed=embed, view=BidButton(ctx.message.id))
    auctions[msg.id] = {"item_name": item_name, "price": starting_price, "user": None, "message": msg}

bot.run(os.environ.get("DISCORD_BOT_TOKEN"))