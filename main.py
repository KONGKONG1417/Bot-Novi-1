import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
from datetime import datetime, timedelta, timezone
import os
import re
import asyncio
import json

# -------- Intents --------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# -------- Configs --------
CONFIG_FILE = "auction_config.json"
AUCTION_FILE = "auctions.json"

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
    "min_bid": 10000,
    "end_time": None
}

auctions = {}  # message_id -> auction data

# -------- Data Persistence --------
def load_config():
    global config
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        save_config()

def save_config():
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)

def load_auctions():
    global auctions
    try:
        with open(AUCTION_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for msg_id, auction in data.items():
                auction["end_time"] = datetime.fromisoformat(auction["end_time"])
                auctions[int(msg_id)] = auction
    except FileNotFoundError:
        pass

def save_auctions():
    data = {}
    for msg_id, auction in auctions.items():
        auction_copy = auction.copy()
        auction_copy["end_time"] = auction["end_time"].isoformat()
        data[str(msg_id)] = auction_copy
    with open(AUCTION_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

# -------- Helpers --------
def parse_time_input(text: str) -> datetime:
    """แปลงข้อความเป็นเวลา รองรับรูปแบบต่างๆ"""
    text = re.sub(r'^(ถึง\s*)', '', text.strip())
    
    formats = [
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M",
        "%H:%M"
    ]
    
    for fmt in formats:
        try:
            t = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                now = datetime.now(timezone.utc)
                dt = datetime(now.year, now.month, now.day, t.hour, t.minute, tzinfo=timezone.utc)
                if dt <= now:
                    dt += timedelta(days=1)
                return dt
            else:
                return t.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    
    raise ValueError("รูปแบบเวลาไม่ถูกต้อง ใช้: 'HH:MM' หรือ 'YYYY-MM-DD HH:MM'")

def build_embed_preview():
    """สร้าง Embed ตัวอย่าง"""
    e = discord.Embed(
        title=draft["title"] or "ตัวอย่างสินค้า",
        description=draft["description"] or "คำอธิบาย",
        color=draft["color"]
    )
    
    if draft.get("thumbnail"):
        e.set_thumbnail(url=draft["thumbnail"])
    if draft.get("image"):
        e.set_image(url=draft["image"])
    if draft.get("footer"):
        e.set_footer(text=draft["footer"])
    if draft.get("end_time"):
        e.add_field(
            name="เวลาสิ้นสุด", 
            value=f"<t:{int(draft['end_time'].timestamp())}:F>",
            inline=False
        )
    e.add_field(name="ราคาขั้นต่ำ", value=f"{draft.get('min_bid', 0):,} บาท", inline=False)
    
    return e

# -------- Modal for Basic Info (5 fields max) --------
class BasicInfoModal(Modal):
    def __init__(self):
        super().__init__(title="ตั้งค่าประมูล (1/2)")
        self.title_input = TextInput(label="ชื่อสินค้า", required=True, max_length=100)
        self.desc_input = TextInput(
            label="คำอธิบาย", 
            style=discord.TextStyle.paragraph, 
            required=True,
            max_length=1024
        )
        self.hex_input = TextInput(
            label="สี Hex", 
            placeholder="#00ff00", 
            required=False,
            max_length=7
        )
        self.minbid_input = TextInput(
            label="ราคาขั้นต่ำ", 
            placeholder="10000", 
            required=True,
            max_length=10
        )
        self.endtime_input = TextInput(
            label="เวลาหมด", 
            placeholder="12:00 หรือ 2025-10-30 15:20",
            required=True
        )
        
        for item in [self.title_input, self.desc_input, self.hex_input, 
                     self.minbid_input, self.endtime_input]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validate color
            if self.hex_input.value:
                draft["color"] = int(self.hex_input.value.lstrip("#"), 16)
            
            # Validate bid
            draft["min_bid"] = int(re.sub(r'[^\d]', '', self.minbid_input.value))
            if draft["min_bid"] <= 0:
                raise ValueError("ราคาต้องมากกว่า 0")
            
            # Validate time
            draft["end_time"] = parse_time_input(self.endtime_input.value)
            if draft["end_time"] <= datetime.now(timezone.utc):
                raise ValueError("เวลาต้องเป็นอนาคต")
            
            draft["title"] = self.title_input.value
            draft["description"] = self.desc_input.value
            
            await interaction.response.send_message(
                "✅ บันทึกข้อมูลแล้ว! ใช้ `/ตกแต่งเพิ่ม` เพื่อเพิ่มรูปภาพ (ถ้าต้องการ)",
                embed=build_embed_preview(),
                ephemeral=True
            )
            
        except ValueError as e:
            await interaction.response.send_message(f"❌ ข้อผิดพลาด: {e}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

# -------- Modal for Images --------
class ImageModal(Modal):
    def __init__(self):
        super().__init__(title="ตกแต่งรูปภาพ (2/2)")
        self.footer_input = TextInput(label="Footer", required=False, max_length=100)
        self.thumb_input = TextInput(label="Thumbnail URL", required=False)
        self.image_input = TextInput(label="Main Image URL", required=False)
        
        for item in [self.footer_input, self.thumb_input, self.image_input]:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        draft["footer"] = self.footer_input.value or None
        draft["thumbnail"] = self.thumb_input.value or None
        draft["image"] = self.image_input.value or None
        
        await interaction.response.send_message(
            "✅ เพิ่มรูปภาพแล้ว!",
            embed=build_embed_preview(),
            ephemeral=True
        )

# -------- Modal for Bidding --------
class BidModal(Modal):
    def __init__(self, msg_id):
        super().__init__(title="ใส่ราคาประมูล")
        self.msg_id = msg_id
        self.price_input = TextInput(
            label="ราคา (บาท)", 
            placeholder="15000", 
            required=True,
            max_length=10
        )
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if self.msg_id not in auctions:
            await interaction.followup.send("❌ ประมูลนี้จบไปแล้ว", ephemeral=True)
            return
        
        auction = auctions[self.msg_id]
        
        # Check if auction ended
        if datetime.now(timezone.utc) >= auction["end_time"]:
            await interaction.followup.send("❌ ประมูลหมดเวลาแล้ว", ephemeral=True)
            return
        
        try:
            bid = int(re.sub(r'[^\d]', '', self.price_input.value))
        except ValueError:
            await interaction.followup.send("❌ กรุณาใส่ตัวเลขเท่านั้น", ephemeral=True)
            return
        
        if bid < auction["min_bid"]:
            await interaction.followup.send(
                f"❌ ราคาต้องไม่ต่ำกว่า {auction['min_bid']:,} บาท",
                ephemeral=True
            )
            return
        
        if bid <= auction["highest_bid"]:
            await interaction.followup.send(
                f"❌ ราคาต้องมากกว่า {auction['highest_bid']:,} บาท",
                ephemeral=True
            )
            return
        
        # Update auction
        auction["highest_bid"] = bid
        auction["highest_user"] = interaction.user.mention
        auction["highest_user_id"] = interaction.user.id
        save_auctions()
        
        # Update embed
        channel = bot.get_channel(auction["channel_id"])
        try:
            msg = await channel.fetch_message(self.msg_id)
            embed = msg.embeds[0]
            embed.clear_fields()
            embed.add_field(
                name="💰 ราคานำ",
                value=f"{auction['highest_bid']:,} บาท โดย {auction['highest_user']}",
                inline=False
            )
            embed.add_field(
                name="⏰ เวลาสิ้นสุด",
                value=f"<t:{int(auction['end_time'].timestamp())}:R>",
                inline=False
            )
            await msg.edit(embed=embed)
        except discord.NotFound:
            await interaction.followup.send("❌ ไม่พบข้อความประมูล", ephemeral=True)
            return
        except discord.HTTPException as e:
            print(f"Error updating embed: {e}")
        
        await interaction.followup.send(
            f"✅ ประมูลสำเร็จ! ราคาของคุณ: {bid:,} บาท",
            ephemeral=True
        )

# -------- Persistent View for Auction --------
class AuctionView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ประมูลเพิ่ม 💰",
        style=discord.ButtonStyle.green,
        custom_id="auction_bid_button"
    )
    async def bid_button(self, interaction: discord.Interaction, button: Button):
        msg_id = interaction.message.id
        
        if msg_id not in auctions:
            await interaction.response.send_message(
                "❌ ประมูลนี้จบไปแล้ว",
                ephemeral=True
            )
            return
        
        await interaction.response.send_modal(BidModal(msg_id))

# -------- Slash Commands --------
@tree.command(name="set_setup_channel", description="ตั้งห้องเซ็ตบอท")
@app_commands.describe(channel="เลือกห้องสำหรับตั้งค่าบอท")
async def set_setup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ ต้องมีสิทธิ์ Manage Server", ephemeral=True)
        return
    
    config["setup_channel_id"] = channel.id
    save_config()
    await interaction.response.send_message(
        f"✅ ตั้งห้องเซ็ตบอทเป็น {channel.mention}",
        ephemeral=True
    )

@tree.command(name="set_auction_channel", description="ตั้งห้องประมูล")
@app_commands.describe(channel="เลือกห้องประมูล")
async def set_auction_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ ต้องมีสิทธิ์ Manage Server", ephemeral=True)
        return
    
    config["auction_channel_id"] = channel.id
    save_config()
    await interaction.response.send_message(
        f"✅ ตั้งห้องประมูลเป็น {channel.mention}",
        ephemeral=True
    )

@tree.command(name="ตกแต่ง", description="ตั้งค่าประมูล (ข้อมูลพื้นฐาน)")
async def decorate(interaction: discord.Interaction):
    if config["setup_channel_id"] and interaction.channel.id != config["setup_channel_id"]:
        await interaction.response.send_message(
            "❌ ต้องใช้ในห้องเซ็ตบอทเท่านั้น",
            ephemeral=True
        )
        return
    
    await interaction.response.send_modal(BasicInfoModal())

@tree.command(name="ตกแต่งเพิ่ม", description="เพิ่มรูปภาพและ footer")
async def decorate_extra(interaction: discord.Interaction):
    if config["setup_channel_id"] and interaction.channel.id != config["setup_channel_id"]:
        await interaction.response.send_message(
            "❌ ต้องใช้ในห้องเซ็ตบอทเท่านั้น",
            ephemeral=True
        )
        return
    
    if not draft["title"]:
        await interaction.response.send_message(
            "❌ กรุณาใช้ `/ตกแต่ง` ก่อน",
            ephemeral=True
        )
        return
    
    await interaction.response.send_modal(ImageModal())

@tree.command(name="ดูตัวอย่าง", description="ดูตัวอย่าง Embed ประมูล")
async def preview(interaction: discord.Interaction):
    if not draft["title"]:
        await interaction.response.send_message(
            "❌ ยังไม่ได้ตั้งค่าประมูล ใช้ `/ตกแต่ง` ก่อน",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(
        embed=build_embed_preview(),
        ephemeral=True
    )

@tree.command(name="เริ่มประมูล", description="โพสต์ประมูลในห้องประมูล")
async def start_auction(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ ต้องมีสิทธิ์ Manage Server", ephemeral=True)
        return
    
    if not config["auction_channel_id"]:
        await interaction.response.send_message(
            "❌ ยังไม่ได้ตั้งห้องประมูล ใช้ `/set_auction_channel` ก่อน",
            ephemeral=True
        )
        return
    
    if not draft["title"] or not draft["description"] or not draft.get("end_time"):
        await interaction.response.send_message(
            "❌ ยังไม่ได้ตั้งค่าประมูลครบ ใช้ `/ตกแต่ง` ก่อน",
            ephemeral=True
        )
        return
    
    channel = bot.get_channel(config["auction_channel_id"])
    
    # Create embed
    embed = discord.Embed(
        title=draft["title"],
        description=draft["description"],
        color=draft["color"]
    )
    
    if draft.get("thumbnail"):
        embed.set_thumbnail(url=draft["thumbnail"])
    if draft.get("image"):
        embed.set_image(url=draft["image"])
    if draft.get("footer"):
        embed.set_footer(text=draft["footer"])
    
    embed.add_field(
        name="💰 ราคานำ",
        value=f"{draft['min_bid']:,} บาท (ยังไม่มีผู้เสนอ)",
        inline=False
    )
    embed.add_field(
        name="⏰ เวลาสิ้นสุด",
        value=f"<t:{int(draft['end_time'].timestamp())}:F> (<t:{int(draft['end_time'].timestamp())}:R>)",
        inline=False
    )
    
    # Send message
    msg = await channel.send(embed=embed, view=AuctionView())
    
    # Store auction data
    auctions[msg.id] = {
        "item": draft["title"],
        "highest_bid": draft["min_bid"],
        "highest_user": "-",
        "highest_user_id": None,
        "min_bid": draft["min_bid"],
        "end_time": draft["end_time"],
        "channel_id": channel.id,
        "color": draft["color"]
    }
    save_auctions()
    
    # Start timer
    bot.loop.create_task(auction_timer(msg.id))
    
    await interaction.response.send_message(
        f"✅ ประมูลโพสต์แล้วใน {channel.mention}\n"
        f"สิ้นสุด: <t:{int(draft['end_time'].timestamp())}:R>",
        ephemeral=True
    )

# -------- Auction Timer --------
async def auction_timer(msg_id):
    """รอจนกว่าประมูลจะจบ แล้วประกาศผู้ชนะ"""
    if msg_id not in auctions:
        return
    
    auction = auctions[msg_id]
    remaining = (auction["end_time"] - datetime.now(timezone.utc)).total_seconds()
    
    if remaining > 0:
        await asyncio.sleep(remaining)
    
    # Get channel and announce winner
    channel = bot.get_channel(auction["channel_id"])
    if not channel:
        return
    
    if auction["highest_user"] == "-":
        await channel.send(
            f"⏰ **ประมูล \"{auction['item']}\" จบแล้ว!**\n"
            f"❌ ไม่มีผู้เสนอราคา"
        )
    else:
        await channel.send(
            f"🎉 **ประมูล \"{auction['item']}\" จบแล้ว!**\n"
            f"🏆 ผู้ชนะ: {auction['highest_user']}\n"
            f"💰 ราคา: **{auction['highest_bid']:,} บาท**"
        )
    
    # Remove from active auctions
    try:
        del auctions[msg_id]
        save_auctions()
    except KeyError:
        pass

# -------- On Ready --------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    
    # Load saved data
    load_config()
    load_auctions()
    
    # Add persistent view
    bot.add_view(AuctionView())
    
    # Restart timers for active auctions
    for msg_id, auction in list(auctions.items()):
        if auction["end_time"] > datetime.now(timezone.utc):
            bot.loop.create_task(auction_timer(msg_id))
            print(f"♻️ Restarted timer for auction {msg_id}")
        else:
            # Already expired, clean up
            del auctions[msg_id]
    
    save_auctions()
    
    # Sync commands
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")

# -------- Run Bot --------
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("❌ ไม่พบ DISCORD_BOT_TOKEN!")
        print("ตั้งค่า environment variable ก่อนรันบอท")
    else:
        bot.run(TOKEN)
