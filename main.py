# main.py
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from datetime import datetime, timedelta
import asyncio
import os
import re

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -----------------------
# Configs in-memory (simple)
# -----------------------
# config_channel: ที่ใช้ตั้งค่า (ห้องเซ็ตบอท)
# auction_channel: ห้องที่จะโพสต์ประมูลจริง
# draft: ข้อมูล Embed ที่ออกแบบในห้องเซ็ตบอท (หนึ่งชุด)
config = {
    "setup_channel_id": None,
    "auction_channel_id": None,
}
draft = {
    "title": None,
    "description": None,
    "color": 0x00ff00,
    "footer": None,
    "thumbnail": None,
    "image": None,
    "min_bid": 10000,   # ค่าเริ่มต้น
    "end_time": None    # datetime
}
auctions = {}  # message_id -> auction dict

# -----------------------
# Helpers
# -----------------------
def parse_time_input(text: str) -> datetime:
    """
    รับ text เช่น:
      - "ถึง12:00" หรือ "12:00"  -> เวลา today หรือ next day ถ้าเวลาแล้ว
      - "2025-10-30 15:20" -> full datetime (server timezone assumed UTC naive)
    คืนค่า datetime (UTC naive) หรือ raise ValueError
    """
    text = text.strip()
    # ถ้ามี prefix 'ถึง' เอาออก
    text = re.sub(r'^(ถึง\s*)', '', text)
    # ลอง parse full datetime first
    formats = ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%d-%m-%Y %H:%M", "%H:%M"]
    for fmt in formats:
        try:
            t = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                # ให้เป็น today/time หรือ next day ถ้าเวลาผ่านไปแล้ว
                now = datetime.utcnow()
                candidate = datetime(now.year, now.month, now.day, t.hour, t.minute)
                if candidate <= now:
                    candidate = candidate + timedelta(days=1)
                return candidate
            else:
                # full date -> return as-is (assume UTC naive)
                return t
        except Exception:
            continue
    raise ValueError("รูปแบบเวลาไม่ถูกต้อง — ยอมรับ 'HH:MM' หรือ 'YYYY-MM-DD HH:MM' หรือ 'ถึงHH:MM'")

def build_preview_embed_from_draft():
    e = discord.Embed(
        title=draft["title"] or "ตัวอย่างสินค้า",
        description=draft["description"] or "คำอธิบาย",
        color=int(draft["color"])
    )
    if draft.get("thumbnail"):
        e.set_thumbnail(url=draft["thumbnail"])
    if draft.get("image"):
        e.set_image(url=draft["image"])
    if draft.get("footer"):
        e.set_footer(text=draft["footer"])
    if draft.get("end_time"):
        e.add_field(name="เวลาสิ้นสุด", value=draft["end_time"].strftime("%Y-%m-%d %H:%M UTC"), inline=False)
    e.add_field(name="ราคาขั้นต่ำ", value=str(draft.get("min_bid", 0)), inline=False)
    return e

# -----------------------
# Modal: ตั้งค่า Embed / สร้างประมูล (Mimu-like)
# -----------------------
class AuctionSetupModal(Modal):
    def __init__(self):
        super().__init__(title="ตั้งค่า Embed ประมูล (Mimu style)")
        self.title_input = TextInput(label="ชื่อสินค้า / Title", required=True)
        self.desc_input = TextInput(label="Description", style=discord.TextStyle.paragraph, required=True)
        self.hex_input = TextInput(label="Hex Color (#RRGGBB)", placeholder="#00ff00", required=True)
        self.footer_input = TextInput(label="Footer (Optional)", required=False)
        self.thumb_input = TextInput(label="Thumbnail URL (Optional)", required=False)
        self.image_input = TextInput(label="Main Image URL (Optional)", required=False)
        self.minbid_input = TextInput(label="ราคาขั้นต่ำ (ตัวเลข)", placeholder="10000", required=True)
        self.endtime_input = TextInput(label="เวลาหมด (เช่น 'ถึง12:00' หรือ '2025-10-30 15:20')", required=True)
        for it in [self.title_input, self.desc_input, self.hex_input, self.footer_input,
                   self.thumb_input, self.image_input, self.minbid_input, self.endtime_input]:
            self.add_item(it)

    async def on_submit(self, interaction: discord.Interaction):
        # เฉพาะผู้ที่รันคำสั่งใน setup channel เท่านั้น
        if config["setup_channel_id"] and interaction.channel.id != config["setup_channel_id"]:
            await interaction.response.send_message("❌ กรุณารันคำสั่งตั้งค่าในห้องที่ตั้งเป็นห้องเซ็ตบอทเท่านั้น", ephemeral=True)
            return

        # ปรับค่า draft จาก modal
        try:
            color_s = self.hex_input.value.lstrip("#")
            color_int = int(color_s, 16)
        except Exception:
            await interaction.response.send_message("❌ ค่าสีไม่ถูกต้อง (ต้องเป็น #RRGGBB)", ephemeral=True)
            return

        try:
            min_bid = int(re.sub(r'[^\d]', '', self.minbid_input.value))
        except Exception:
            await interaction.response.send_message("❌ ราคาขั้นต่ำต้องเป็นตัวเลขเท่านั้น", ephemeral=True)
            return

        # parse end time
        try:
            end_dt = parse_time_input(self.endtime_input.value)
        except Exception as ex:
            await interaction.response.send_message(f"❌ ไม่สามารถอ่านเวลาได้: {ex}", ephemeral=True)
            return

        # update draft
        draft["title"] = self.title_input.value
        draft["description"] = self.desc_input.value
        draft["color"] = color_int
        draft["footer"] = self.footer_input.value or None
        draft["thumbnail"] = self.thumb_input.value or None
        draft["image"] = self.image_input.value or None
        draft["min_bid"] = min_bid
        draft["end_time"] = end_dt

        # ส่ง preview ในช่อง setup (edit message ถ้ามี)
        embed = build_preview_embed_from_draft()
        # send or edit preview
        await interaction.response.send_message("✅ เซ็ต Embed เสร็จแล้ว — นี่คือตัวอย่าง (เฉพาะในห้องเซ็ตบอท)", embed=embed, ephemeral=True)

# -----------------------
# Modal: ใส่ราคา (bid)
# -----------------------
class BidModal(Modal):
    def __init__(self, message_id):
        super().__init__(title="ใส่ราคาประมูล")
        self.message_id = message_id
        self.price_input = TextInput(label="ราคา (ตัวเลข)", placeholder="ตัวอย่าง: 15000", required=True)
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)  # ป้องกัน Interaction Failed
        if self.message_id not in auctions:
            await interaction.followup.send("❌ ประมูลนี้หาไม่เจอหรือสิ้นสุดแล้ว", ephemeral=True)
            return
        auction = auctions[self.message_id]
        try:
            bid = int(re.sub(r'[^\d]', '', self.price_input.value))
        except Exception:
            await interaction.followup.send("❌ ใส่ตัวเลขเท่านั้น", ephemeral=True)
            return

        if bid < auction["min_bid"]:
            await interaction.followup.send(f"❌ ราคาต้องไม่ต่ำกว่า {auction['min_bid']}", ephemeral=True)
            return
        if bid <= auction["highest_bid"]:
            await interaction.followup.send(f"❌ ราคาต้องสูงกว่าราคานำปัจจุบัน {auction['highest_bid']}", ephemeral=True)
            return

        # อัปเดตราคานำ
        auction["highest_bid"] = bid
        auction["highest_user"] = interaction.user.mention
        # อัปเดต embed ใน message
        channel = bot.get_channel(auction["channel_id"])
        try:
            msg = await channel.fetch_message(self.message_id)
            embed = msg.embeds[0] if msg.embeds else discord.Embed(title=f"ประมูล: {auction['item']}")
            # update field or replace
            embed.clear_fields()
            embed.add_field(name="ราคานำ", value=f"{auction['highest_bid']} โดย {auction['highest_user']}", inline=False)
            # keep other visuals by copying from draft stored reference if any
            # For simplicity, we'll keep title/description/color in auction record
            embed.title = f"ประมูล: {auction['item']}"
            embed.color = auction.get("color", 0x00ff00)
            await msg.edit(embed=embed)
        except Exception:
            pass

        await interaction.followup.send(f"✅ ประมูลสำเร็จ — ราคาของคุณนำแล้ว {bid}", ephemeral=True)

# -----------------------
# Views / Buttons
# -----------------------
class AuctionView(View):
    def __init__(self, message_id):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="ประมูลเพิ่ม 💰", style=discord.ButtonStyle.green)
    async def bid_button(self, interaction: discord.Interaction, button: Button):
        # เปิด modal เก็บราคา
        await interaction.response.send_modal(BidModal(self.message_id))

# -----------------------
# Slash Commands (Thai names)
# -----------------------

# set setup channel (ห้องสำหรับตกแต่ง)
@tree.command(name="set_setup_channel", description="ตั้งห้องที่ใช้ตกแต่ง/เซ็ตบอท (ห้องเซ็ตบอท)")
@app_commands.describe(channel="เลือกห้องสำหรับตั้งค่า")
async def set_setup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ (ต้องมี Manage Server)", ephemeral=True)
        return
    config["setup_channel_id"] = channel.id
    await interaction.response.send_message(f"✅ ตั้งห้องเซ็ตบอทเป็น {channel.mention}", ephemeral=True)

# set auction channel (ห้องที่จะโพสต์ประมูลจริง)
@tree.command(name="set_auction_channel", description="ตั้งห้องที่จะโพสต์ประมูลจริง")
@app_commands.describe(channel="เลือกห้องสำหรับโพสต์ประมูล")
async def set_auction_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ (ต้องมี Manage Server)", ephemeral=True)
        return
    config["auction_channel_id"] = channel.id
    await interaction.response.send_message(f"✅ ตั้งห้องประมูลเป็น {channel.mention}", ephemeral=True)

# /ตกแต่ง (เปิด modal ในห้อง setup)
@tree.command(name="ตกแต่ง", description="ตกแต่ง Embed ประมูล (ต้องรันในห้องเซ็ตบอท)")
async def decorate(interaction: discord.Interaction):
    if config["setup_channel_id"] and interaction.channel.id != config["setup_channel_id"]:
        await interaction.response.send_message("❌ คำสั่งนี้ต้องรันในห้องเซ็ตบอทที่ตั้งไว้เท่านั้น", ephemeral=True)
        return
    await interaction.response.send_modal(AuctionSetupModal())

# /เริ่มประมูล -> โพสต์ในห้อง auction (ใช้ draft)
@tree.command(name="เริ่มประมูล", description="โพสต์ประมูลตามที่ตกแต่งไว้ ไปยังห้องประมูล")
async def start_auction(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เริ่มประมูล (ต้องมี Manage Server)", ephemeral=True)
        return
    if not config["auction_channel_id"]:
        await interaction.response.send_message("❌ กรุณาตั้งห้องประมูลก่อนด้วย /set_auction_channel", ephemeral=True)
        return
    # ตรวจว่ามี draft สำคัญ
    if not draft["title"] or not draft["description"] or not draft.get("end_time"):
        await interaction.response.send_message("❌ ยังไม่ได้ตกแต่งหรือยังไม่ได้ตั้งเวลา กรุณาใช้ /ตกแต่ง ในห้องเซ็ตบอทก่อน", ephemeral=True)
        return

    channel = bot.get_channel(config["auction_channel_id"])
    if not channel:
        await interaction.response.send_message("❌ หา channel ไม่เจอ", ephemeral=True)
        return

    # สร้าง embed ตาม draft แต่เฉพาะข้อมูลที่จำเป็น (ในห้องประมูลจะมีแค่ปุ่มประมูล)
    embed = discord.Embed(title=f"{draft['title']}", description=draft['description'], color=draft['color'])
    if draft.get("thumbnail"): embed.set_thumbnail(url=draft["thumbnail"])
    if draft.get("image"): embed.set_image(url=draft["image"])
    if draft.get("footer"): embed.set_footer(text=draft["footer"])
    embed.add_field(name="ราคานำ", value=f"{draft['min_bid']} โดย –", inline=False)
    embed.add_field(name="เวลาสิ้นสุด", value=draft["end_time"].strftime("%Y-%m-%d %H:%M UTC"), inline=False)

    msg = await channel.send(embed=embed, view=AuctionView(0))
    auctions[msg.id] = {
        "item": draft["title"],
        "highest_bid": draft["min_bid"],
        "highest_user": "-",
        "min_bid": draft["min_bid"],
        "end_time": draft["end_time"],
        "channel_id": channel.id,
        "color": draft["color"]
    }
    # update view with real message id
    view = AuctionView(msg.id)
    await msg.edit(view=view)

    # start timer
    bot.loop.create_task(auction_timer(msg.id))
    await interaction.response.send_message(f"✅ ประมูลถูกโพสต์ใน {channel.mention} เรียบร้อย!", ephemeral=True)

# convenience commands to set min_bid and time in config (optional)
@tree.command(name="ขั้นต่ำ", description="ตั้งราคาประมูลขั้นต่ำ (สำหรับการตั้งก่อนสร้าง)")
@app_commands.describe(amount="จำนวนเงิน (ตัวเลข)")
async def cmd_set_min(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์", ephemeral=True)
        return
    config["min_bid"] = amount
    draft["min_bid"] = amount
    await interaction.response.send_message(f"✅ ตั้งขั้นต่ำเป็น {amount}", ephemeral=True)

@tree.command(name="ตั้งเวลา", description="ตั้งเวลาเริ่มต้นสำหรับ draft (นาที) — ใช้ /ตกแต่ง เพื่อกำหนดเวลาจริง")
@app_commands.describe(minutes="นาที")
async def cmd_set_duration(interaction: discord.Interaction, minutes: int):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์", ephemeral=True)
        return
    config["auction_duration"] = minutes
    await interaction.response.send_message(f"✅ ตั้ง default ระยะเวลาเป็น {minutes} นาที", ephemeral=True)

# -----------------------
# Auction timer
# -----------------------
async def auction_timer(message_id):
    if message_id not in auctions:
        return
    auction = auctions[message_id]
    now = datetime.utcnow()
    remaining = (auction["end_time"] - now).total_seconds()
    if remaining > 0:
        await asyncio.sleep(remaining)
    # announce winner
    channel = bot.get_channel(auction["channel_id"])
    if not channel:
        return
    winner = auction.get("highest_user", "-")
    amount = auction.get("highest_bid", 0)
    await channel.send(f"⏰ ประมูลสินค้าจบแล้ว! ผู้ชนะคือ {winner} ด้วยราคา {amount} 💰")
    # cleanup
    try:
        del auctions[message_id]
    except KeyError:
        pass

# -----------------------
# on_ready -> sync commands
# -----------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await tree.sync()
        print(f"Slash Commands synced ({len(synced)})")
    except Exception as e:
        print("Sync error:", e)

# -----------------------
# run bot
# -----------------------
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN env var missing")
    else:
        bot.run(TOKEN)
