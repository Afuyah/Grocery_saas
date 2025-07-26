from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, current_app, g, Response, abort
from flask_login import login_user, logout_user, current_user, login_required
from sqlalchemy import func
from app.models import User,  Role, Sale, Product, Category, StockLog, CartItem, Shop, Business
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta, datetime
from sqlalchemy.exc import SQLAlchemyError
from app.utils.render import render_htmx
from .forms import RegistrationForm
from urllib.parse import urlparse, urljoin
import logging
from app import db, csrf, role_required, shop_access_required, business_access_required
from .schemas import RegistrationSchema
auth_bp = Blueprint('auth', __name__)

# Create a logger instance
logger = logging.getLogger(__name__)



def is_safe_url(target):
    """Prevent open redirects"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def _handle_login_error(message, is_json, field=None):
    if is_json:
        response = {'success': False, 'message': message}
        if field:
            response['field'] = field
        return jsonify(response), 401
    flash(message, 'error')
    return redirect(url_for('auth.login'))

def _handle_login_success(redirect_url, user, is_json):
    if is_json:
        return jsonify({
            'success': True,
            'redirect_url': redirect_url,
            'user': {
                'id': user.id,
                'username': user.username,
                'role': user.role.value
            }
        }), 200
    return redirect(redirect_url)

def _determine_redirect_url(user, next_param):
    from flask import session

    # 1. Safe "next" param
    if next_param and is_safe_url(next_param):
        return next_param

    # 2. Superadmin → Full platform
    if user.is_superadmin():
        return url_for('bhapos.superadmin_dashboard')

    # 3. Tenant → Must own a business
    elif user.is_tenant():
        business = Business.query.filter_by(id=user.business_id, is_deleted=False).first()
        if business:
            session['active_business_id'] = business.id
            return url_for('bhapos.tenant_dashboard')  # ✅ Redirect here
        abort(400, "Tenant does not own a valid business")

    # 4. Admin → Must be assigned to a shop
    elif user.is_admin():
        if user.shop_id:
            session['active_shop_id'] = user.shop_id
            return url_for('admin.admin_dashboard', shop_id=user.shop_id)
        abort(400, "Admin has no assigned shop")

    # 5. Cashier → Shop sales screen
    elif user.is_cashier():
        if user.shop_id:
            return url_for('sales.sales_screen', shop_id=user.shop_id)
        abort(400, "Cashier is not assigned to a shop")

    # 6. Fallback
    return url_for('main.home')



@csrf.exempt
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    logger.info("Login route accessed.")

    if request.method == 'POST':
        # Extract credentials
        if request.is_json:
            data = request.get_json()
            username = data.get('username')
            password = data.get('password')
        else:
            username = request.form.get('username')
            password = request.form.get('password')

        # Validate input
        if not username or not password:
            logger.warning("Missing username or password")
            return _handle_login_error("Username and password are required", request.is_json, field='username')

        username = username.strip().lower()
        user = User.query.filter_by(username=username).first()

        # Authentication check
        if not user or not user.check_password(password):
            logger.warning(f"Failed login attempt for {username}")
            field = 'username' if not user else 'password'
            return _handle_login_error("Invalid credentials", request.is_json, field=field)

        # Account status check
        if user.is_deleted:
            logger.warning(f"Login attempt for deleted user: {username}")
            return _handle_login_error("Account is disabled", request.is_json)

        # Log in user
        login_user(user, remember=True)
        logger.info(f"User {username} logged in successfully.")

        # Determine redirect and store active_shop_id
        redirect_url = _determine_redirect_url(user, request.args.get('next'))

        return _handle_login_success(redirect_url, user, request.is_json)

    # GET login form
    next_page = request.args.get('next')
    return render_template('auth/login.html', next=next_page)



from flask import url_for
from urllib.parse import urljoin

@auth_bp.route('/api/shops/<int:shop_id>/registration-link', methods=['GET'])
@login_required
def get_registration_link(shop_id):
    
    shop = Shop.query.get_or_404(shop_id)
    
    # Verify user has permission (shop owner/admin)
    if not (current_user.is_tenant() or 
            (current_user.is_admin() and current_user.shop_id == shop_id)):
        return jsonify({
            'success': False,
            'error': 'Not authorized to generate registration links for this shop'
        }), 403
    
    # Generate the full registration URL
    registration_url = url_for(
        'auth.register_user', 
        shop_slug=shop.slug, 
        _external=True
    )
    
    # For POS systems that need a short link
    short_code = generate_short_code()  
    short_url = urljoin(request.host_url, f'r/{short_code}')
    
    # Store the mapping if needed (in Redis or database)
    cache.set(f'short_code:{short_code}', shop.slug, timeout=86400)  # 24 hours
    
    return jsonify({
        'success': True,
        'registration_url': registration_url,
        'short_url': short_url,
        'qr_code_url': f'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={registration_url}',
        'shop': {
            'id': shop.id,
            'name': shop.name,
            'slug': shop.slug
        }
    })





def create_cashier_user(data, shop):
    """Create a new cashier user with validated data"""
    return User(
        username=data['username'].lower().strip(),
        email=data.get('email', '').lower().strip() or None,
        first_name=data.get('first_name', '').strip(),
        last_name=data.get('last_name', '').strip(),
        phone=data.get('phone', '').strip(),
        shop_id=shop.id,
        business_id=shop.business_id,
        role=Role.CASHIER,
        is_active=True
    ).set_password(data['password'])

def handle_registration_success(user, shop):
    """Handle successful registration response with auto-login and address check"""
    logger.info(f"New cashier registered: {user.username} for shop {shop.id} (slug: {shop.slug})")
    
    # Auto-login the user
    login_user(user)
    user.record_login()  # Update last_login_at
    db.session.commit()

    # Check if address is set
    redirect_url = url_for('auth.set_address') if not user.address else url_for('sales.sales_screen', shop_id=shop.id)

    if request.is_json:
        return jsonify({
            'success': True,
            'user': user.serialize(),
            'redirect': redirect_url
        }), 201
    
    flash('Registration successful!', 'success')
    return Response(
        headers={"HX-Redirect": redirect_url}
    )


def handle_registration_error(message, shop, form=None):
    """Handle registration error response"""
    db.session.rollback()
    
    if request.is_json:
        return jsonify({
            'success': False,
            'error': message
        }), 400
    
    flash(message, 'error')
    return render_htmx(
        'auth/register.html',
        shop=shop,
        form=form or RegistrationForm(),
        errors=message if isinstance(message, dict) else None
    )


@auth_bp.route('/register/<shop_slug>', methods=['GET', 'POST'])
def register_user(shop_slug):
    """
    Public registration endpoint for users to register as CASHIER for a shop.
    Auto-logs in the user and redirects to address setup if address is not set,
    otherwise to the purchase screen.
    """
    try:
        # Find active, non-deleted shop by slug
        shop = Shop.query.filter_by(slug=shop_slug, is_active=True, is_deleted=False).first()
        if not shop:
            logger.warning(f"Registration attempt for non-existent or inactive shop slug: {shop_slug}")
            if request.is_json:
                return jsonify({'success': False, 'error': 'Shop not found'}), 404
            return render_htmx('auth/register.html', shop=None), 404

        if not shop.allow_registrations:
            logger.info(f"Registration attempt for shop {shop.name} (slug: {shop_slug}) with registrations disabled")
            if request.is_json:
                return jsonify({'success': False, 'error': 'Registrations are currently disabled for this shop'}), 403
            return render_htmx('auth/register.html', shop=shop, registration_disabled=True), 403

        if request.method == 'POST':
            if request.is_json:
                data = request.get_json(silent=True)
                if not data:
                    return handle_registration_error("Invalid JSON data", shop)
                form = RegistrationForm(data=data)
            else:
                form = RegistrationForm()

            if form.validate_on_submit():
                try:
                    # Create user using helper function
                    user = create_cashier_user(form.data, shop)
                    db.session.add(user)
                    db.session.commit()
                    return handle_registration_success(user, shop)

                except IntegrityError:
                    logger.warning(f"Duplicate username or email for shop {shop.id}: {form.username.data}/{form.email.data}")
                    if request.is_json:
                        return jsonify({'success': False, 'error': 'Username or email already exists'}), 409
                    form.username.errors.append('Username or email already exists')
                    return handle_registration_error(form.errors, shop, form)

                except Exception as e:
                    logger.error(f"Error registering user for shop {shop.id}: {str(e)}")
                    return handle_registration_error("Registration failed. Please try again later.", shop, form)

            # Form validation failed
            if request.is_json:
                return jsonify({'success': False, 'errors': form.errors}), 400
            return render_htmx('auth/register.html', form=form, shop=shop)

        # GET: Render registration form
        return render_htmx('auth/register.html', form=RegistrationForm(), shop=shop)

    except Exception as e:
        logger.critical(f"Unexpected registration error for shop slug {shop_slug}: {str(e)}", exc_info=True)
        if request.is_json:
            return jsonify({'success': False, 'error': 'Unexpected error occurred'}), 500
        return render_htmx('auth/register.html', error="Unexpected error occurred.", shop=None), 500




@csrf.exempt
@auth_bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    logger.info("Change password route accessed.")
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        # Validate current password
        if not current_user.check_password(current_password):
            flash('Current password is incorrect.', category='error')
            logger.warning(f"User {current_user.username} provided incorrect current password.")
            return render_template('auth/fragments/_admin_change_password.html')

        if new_password != confirm_password:
            flash('New passwords do not match.', category='error')
            logger.warning("New password confirmation did not match.")
            return render_template('auth/fragments/_admin_change_password.html')

        if len(new_password) < 5:
            flash('New password must be at least 5 characters long.', category='error')
            logger.warning("New password length is insufficient.")
            return render_template('auth/fragments/_admin_change_password.html')

        current_user.set_password(new_password)
        db.session.commit()
        flash('Your password has been updated successfully!', category='success')
        logger.info(f"User {current_user.username} updated their password.")

        # After successful password update, redirect or return a success partial
        return render_template('auth/fragments/_admin_change_password.html')

    # For GET request, render the form fragment
    template_name = 'auth/fragments/_admin_change_password.html'
    return render_template(template_name)


@auth_bp.route('/user_management/<int:shop_id>', methods=['GET'])
@login_required
@shop_access_required 
@role_required(Role.ADMIN, Role.TENANT) 
def user_management(shop_id):
    logger.info(f"User management accessed by user {current_user.username}.")

    # Get the current shop from g (set by shop_access_required)
    shop = g.current_shop

    # Fetch users belonging only to this shop
    users = User.query.filter_by(shop_id=shop.id, is_deleted=False).all()

    if request.headers.get('HX-Request'):
        logger.info(f"[HTMX] Fragment request for user management by {current_user.username}")
        return render_template('auth/fragments/_user_management_fragment.html', users=users,)

    return render_template(
        'auth/admin_dashboard.html',
        fragment_template='auth/fragments/_user_management_fragment.html',
        users=users
    )

@csrf.exempt
@auth_bp.route('/shops/<int:shop_id>/add_user', methods=['GET', 'POST'])
@login_required
@role_required(Role.ADMIN, Role.TENANT)
@shop_access_required  # Ensures the shop belongs to the current admin or tenant
def add_user(shop_id):
    shop = g.current_shop
    logger.info(f"Add user route accessed by user {current_user.username} for shop {shop.id}.")

    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password')
        role = request.form.get('role')

        # --- Validations ---
        if username.isdigit():
            return render_template('auth/fragments/add_user_fragment.html', error="Username cannot be only numbers.", shop=shop)

        if role.upper() not in Role.__members__:
            return render_template('auth/fragments/add_user_fragment.html', error="Invalid role selected.", shop=shop)

        if User.query.filter_by(username=username).first():
            return render_template('auth/fragments/add_user_fragment.html', error="Username already exists.", shop=shop)

        # --- Create new user ---
        new_user = User(
            username=username,
            role=Role[role.upper()],
            shop_id=shop.id,
            business_id=shop.business_id  # ensure scoped correctly
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        logger.info(f"User {username} created for shop {shop.name}.")

        return Response(headers={"HX-Redirect": url_for('auth.user_management', shop_id=shop.id)})

    return render_template('auth/fragments/add_user_fragment.html', shop=shop)



@auth_bp.route('/logout')
@login_required
def logout():
    logger.info(f"User {current_user.username} logged out.")
    
    # Clear the session (including cart data)
    session.clear()
    
    logout_user()  # Log out the user
    flash('Logged out successfully.', 'success')
    return redirect(url_for('home.index'))


@auth_bp.route('/api/auth/current-user')
@login_required
def current_user_info():
    return jsonify({
        'id': current_user.id,
        'name': current_user.username,
        
        'role': current_user.role.value
    })
