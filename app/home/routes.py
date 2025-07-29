# app/home.py
from flask import Blueprint, render_template
from flask_login import current_user
import logging
from app.utils.render import render_htmx
from app.models import Shop,User

# Define the Blueprint
home_bp = Blueprint('home', __name__)

# Create a logger instance
logger = logging.getLogger(__name__)

# home/routes.py
@home_bp.route('/')
def index():
    shop_data = None
    if current_user.is_authenticated:
        if current_user.is_superadmin():
            # Handle superadmin case
            pass
        elif current_user.shop_id:
            shop_data = Shop.query.get(current_user.shop_id)
        elif current_user.business_id:
            # Handle business owner case
            pass
    
    return render_template('home.html', current_shop=shop_data)
