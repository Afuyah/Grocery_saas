from flask import request, jsonify, render_template
from flask.views import MethodView
from flask_login import login_required, current_user
from marshmallow import ValidationError
from decimal import Decimal, InvalidOperation
from sqlalchemy import and_
from .repositories import ProductRepository, CategoryRepository, SaleRepository
from .services import (
    SalesService,

    ProductService,
    ReceiptService,
    CategoryService,
    PaymentService,
    TaxService
  
)
from .schemas import (
    CheckoutSchema,
    CartItemSchema,
    ProductSearchSchema,
    ReceiptSchema
    
)
from app import shop_access_required, role_required, csrf
from app.models import Role, Shop, Category, Product
import logging

logger = logging.getLogger(__name__)

class SalesController(MethodView):
    decorators = [login_required, shop_access_required]

    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def get(self, shop_id):
        """Render POS page (products/categories are loaded via API)"""
        try:
            shop = Shop.query.get_or_404(shop_id)
            return render_template('sales/pos.html', shop=shop.serialize())
        except Exception as e:
            logger.error(f"Failed to load POS UI: {str(e)}", exc_info=True)
            return render_template('sales/error.html', error="Failed to load POS page"), 500


class ProductAPIController(MethodView):
    decorators = [login_required, shop_access_required]

    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def get(self, shop_id):
        """API: Return products sorted by most sold"""
        try:
            products = ProductService.get_available_for_sale(shop_id)
            return jsonify(products)
        except Exception as e:
            logger.error(f"Failed to load products: {str(e)}", exc_info=True)
            return jsonify({'error': 'Failed to load products'}), 500


            
class ProductSearchAPIController(MethodView):
    decorators = [login_required, shop_access_required, csrf.exempt]

    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def post(self, shop_id):
        try:
            data = ProductSearchSchema().load(request.json)

            query = (data['query'] or '').strip()[:100]
            category_id = data.get('category_id')

            if not query:
                return jsonify([])

            products = ProductService.search(
                shop_id=shop_id,
                query=query,
                category_id=category_id,
                limit=20  # optional limit
            )

            return jsonify(products)

        except ValidationError as ve:
            return jsonify({'error': 'Invalid input', 'details': ve.messages}), 400
        except Exception as e:
            logger.exception("Product search error")
            return jsonify({'error': 'Search failed'}), 500




class CategoryAPIController(MethodView):
    decorators = [login_required, shop_access_required]

    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def get(self, shop_id):
        """API: Get active categories and their products"""
        try:
            categories = CategoryRepository.get_for_pos(shop_id)
            serialized = []
            
            for category in categories:
                cat_data = category.serialize()
                cat_data['products'] = [
                    p.serialize(for_pos=True) 
                    for p in sorted(category.products, key=lambda p: p.name)
                    if p.is_active and p.stock > 0
                ]
                serialized.append(cat_data)
                
            return jsonify(serialized)
        except Exception as e:
            logger.error(f"Failed to fetch categories: {str(e)}", exc_info=True)
            return jsonify({'error': 'Failed to load categories'}), 500



class TransactionController(MethodView):
    decorators = [login_required, shop_access_required, csrf.exempt]
    
    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def get(self, shop_id, sale_id=None):
        try:
            if request.path.endswith('/recent'):
                # ✅ Recent 2 sales
                transactions = SalesService.get_recent_transactions(shop_id)
                return jsonify([{
                    'id': t.id,
                    'date': t.date.isoformat(),
                    'total': t.total,
                    'payment_method': t.payment_method,
                    'customer_name': t.customer_name
                } for t in transactions])

            elif sale_id:
                # ✅ Full cart details of a specific sale for reorder
                sale = SalesService.get_sale_details_for_reorder(sale_id, shop_id)
                if not sale:
                    return jsonify({'error': 'Sale not found'}), 404

                return jsonify({
                    'id': sale.id,
                    'date': sale.date.isoformat(),
                    'customer_name': sale.customer_name,
                    'payment_method': sale.payment_method,
                    'cart_items': [{
                        'product_id': item.product.id,
                        'product_name': item.product.name,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'subtotal': item.quantity * item.unit_price
                    } for item in sale.cart_items]
                })

            else:
                # 🔁 Paginated transaction history
                page = request.args.get('page', 1, type=int)
                per_page = request.args.get('per_page', 20, type=int)
                date_from = request.args.get('date_from')
                date_to = request.args.get('date_to')

                transactions = SalesService.get_transactions(
                    shop_id=shop_id,
                    page=page,
                    per_page=per_page,
                    date_from=date_from,
                    date_to=date_to
                )
                return jsonify({
                    'transactions': [t.to_dict() for t in transactions.items],
                    'total': transactions.total,
                    'pages': transactions.pages,
                    'current_page': page
                })

        except Exception as e:
            logger.error(f"Failed to get transactions: {str(e)}")
            return jsonify({'error': 'Failed to get transactions'}), 500

            
    
    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def post(self, shop_id):
        """
        Process checkout - POST /shops/<shop_id>/transactions
        """
        try:
            checkout_data = CheckoutSchema().load(request.json)

            result = SalesService.process_checkout(
                shop_id=shop_id,
                user_id=current_user.id,
                cart_items=checkout_data['cart_items'], 
                payment_method=checkout_data['payment_method'],
                customer_data={
                    'name': checkout_data.get('customer_name'),
                    'phone': checkout_data.get('customer_phone')
                }
            )

            return jsonify({
                'success': True,
                'sale_id': result['sale_id'],
                'receipt': result['receipt'],
                'amount_paid': result['amount_paid']
            })

        except ValidationError as e:
            return jsonify({'error': e.messages}), 400
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"Checkout failed: {str(e)}")
            return jsonify({'error': 'Checkout processing failed'}), 500


class ReceiptController(MethodView):
    decorators = [login_required, shop_access_required]
    
    @role_required(Role.CASHIER, Role.ADMIN, Role.TENANT)
    def get(self, shop_id):
        """
        Get receipt - GET /api/shops/<shop_id>/receipts
        """
        try:
            data = ReceiptSchema().load(request.args)
            receipt = ReceiptService.generate(
                sale_id=data['sale_id'],
                format=data['format']
            )
            return jsonify(receipt)
        except ValidationError as e:
            return jsonify({'error': e.messages}), 400
        except Exception as e:
            logger.error(f"Failed to generate receipt: {str(e)}")
            return jsonify({'error': 'Failed to generate receipt'}), 500
        
        
