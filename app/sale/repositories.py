from flask import current_app
from sqlalchemy import func, desc, and_, or_
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from .. import db
from ..models import Product, Category, Sale, CartItem
from sqlalchemy.orm import contains_eager
from sqlalchemy.orm import joinedload, with_loader_criteria
from decimal import Decimal, InvalidOperation

class ProductRepository:

    @staticmethod
    def get_available_for_sale(shop_id: int) -> List[Product]:
        """
        Get all active products available for sale in the shop,
        sorted by total quantity sold in descending order.
        """
        return (
            db.session.query(Product)
            .join(Category)
            .outerjoin(CartItem, CartItem.product_id == Product.id)
            .outerjoin(Sale, CartItem.sale_id == Sale.id)
            .filter(
                Category.shop_id == shop_id,
                Category.is_active == True,
                Product.is_active == True,
                Product.stock > 0,
                or_(Sale.shop_id == shop_id, Sale.shop_id == None),
                or_(Sale.is_deleted == False, Sale.is_deleted == None),
            )
            .group_by(Product.id)
            .order_by(func.coalesce(func.sum(CartItem.quantity), 0).desc())  # Ensures correct ordering with NULLs
            .all()
        )

    @staticmethod
    def get_for_sale(product_id: int, shop_id: int) -> Optional[Product]:
        """
        Fetch a single product if it's active, belongs to the shop, and is in stock.
        """
        return Product.query.filter_by(
            id=product_id,
            shop_id=shop_id,
            is_active=True
        ).filter(Product.stock > 0).first()

    @staticmethod
    def get_most_sold_products(shop_id: int, limit: int = 20) -> List[Product]:
        """
        Return the top 'limit' most sold products in a shop, sorted by quantity sold.
        Only includes products that are active and in stock.
        """
        return (
            db.session.query(Product)
            .join(CartItem, Product.id == CartItem.product_id)
            .join(Sale, CartItem.sale_id == Sale.id)
            .filter(
                Product.shop_id == shop_id,
                Product.is_active == True,
                Product.stock > 0,
                Sale.shop_id == shop_id,
                Sale.is_deleted == False,
            )
            .group_by(Product.id)
            .order_by(func.sum(CartItem.quantity).desc())
            .limit(limit)
            .all()
        )

        
    @staticmethod
    def search_available(
        shop_id: int, 
        query: str, 
        category_id: Optional[int] = None,
        in_stock_only: bool = True,
        limit: Optional[int] = None
    ) -> List[Product]:
        base_filters = [
            Category.shop_id == shop_id,
            Product.is_active == True,
            Category.is_active == True,
            or_(
                Product.name.ilike(f'%{query}%'),
                Product.barcode.ilike(f'%{query}%'),
                Product.sku.ilike(f'%{query}%')
            )
        ]

        if in_stock_only:
            base_filters.append(Product.stock > 0)

        # Apply filters and ordering first
        search_query = db.session.query(Product).join(Category).filter(
            and_(*base_filters)
        ).order_by(Product.name)

        if category_id:
            search_query = search_query.filter(Product.category_id == category_id)

        # Only apply limit after order_by
        if limit:
            search_query = search_query.limit(limit)

        return search_query.all()



    @staticmethod
    def get_featured_products(shop_id: int, limit: int = 12) -> List[Product]:
        """
        Get frequently purchased products for quick access
        Args:
            shop_id: ID of the shop
            limit: Maximum number of products to return
        Returns:
            List of featured Product objects
        """
        return db.session.query(Product)\
            .join(CartItem, Product.id == CartItem.product_id)\
            .join(Category)\
            .filter(
                and_(
                    Category.shop_id == shop_id,
                    Product.is_active == True,
                    Product.stock > 0,
                    CartItem.sale_id != None  # Only products that have been sold
                )
            )\
            .group_by(Product.id)\
            .order_by(func.count(CartItem.id).desc())\
            .limit(limit)\
            .all()
    @staticmethod
    def get_bulk_for_sale(product_ids: List[int], shop_id: int):
        products = Product.query.filter(
            Product.id.in_(product_ids),
            Product.shop_id == shop_id,
            Product.is_deleted == False
        ).all()

        return products
                
    @staticmethod
    def update_stock(product_id: int, quantity_change: int) -> bool:
        """
        Update product stock levels
        Args:
            product_id: ID of product to update
            quantity_change: Amount to adjust stock by (positive or negative)
        Returns:
            True if successful, False otherwise
        """
        try:
            product = db.session.query(Product).get(product_id)
            if not product:
                return False
                
            new_stock = product.stock + quantity_change
            if new_stock < 0:
                return False  # Prevent negative stock
                
            product.stock = new_stock
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            return False





class CategoryRepository:
    @staticmethod
    def get_for_pos(shop_id: int) -> List[Category]:
        """
        Get categories with their active, in-stock products for POS display
        """
        return db.session.query(Category)\
            .filter(
                and_(
                    Category.shop_id == shop_id,
                    Category.is_active == True
                )
            )\
            .options(
                joinedload(Category.products)
                    .load_only(
                        Product.id,
                        Product.name,
                        Product.selling_price,
                        Product.image_url,
                        Product.stock,
                        Product.barcode,
                        Product.category_id
                    ),
                with_loader_criteria(
                    Product,
                    lambda p: and_(p.is_active == True, p.stock > 0),
                    include_aliases=True
                )
            )\
            .order_by(Category.position, Category.name)\
            .all()

    @staticmethod
    def get_ranked_categories(shop_id: int, limit: int = None) -> List[Category]:
        """
        Get categories ordered by total sales of their products.
        """
        query = db.session.query(Category)\
            .join(Product, Category.products)\
            .join(CartItem, CartItem.product_id == Product.id)\
            .join(Sale, CartItem.sale_id == Sale.id)\
            .filter(
                Category.shop_id == shop_id,
                Category.is_active == True,
                Product.is_active == True,
                Product.stock > 0,
                Sale.is_deleted == False,
                Sale.shop_id == shop_id
            )\
            .group_by(Category.id)\
            .order_by(func.sum(CartItem.quantity).desc())

        if limit:
            query = query.limit(limit)

        return query.all()


    @staticmethod
    def get_category_with_products(shop_id: int, category_id: int) -> Optional[Category]:
        """
        Get a single category with its active products
        Args:
            shop_id: ID of the shop
            category_id: ID of the category to retrieve
        Returns:
            Category object if found, None otherwise
        """
        return db.session.query(Category)\
            .filter(
                and_(
                    Category.id == category_id,
                    Category.shop_id == shop_id,
                    Category.is_active == True
                )
            )\
            .options(
                db.joinedload(Category.products)
                .filter(Product.is_active == True)
                .order_by(Product.name)
            )\
            .first()


class SaleRepository:
    @staticmethod
    def get_recent_sales(shop_id: int, limit: int = 5) -> List[Sale]:
        """Get recent sales (no session filtering)"""
        return db.session.query(Sale)\
            .filter(Sale.shop_id == shop_id)\
            .order_by(Sale.date.desc())\
            .limit(limit).all()

    @staticmethod
    def get_sale_with_items(sale_id: int, shop_id: int) -> Optional[Sale]:
        """Get complete sale data with items"""
        return db.session.query(Sale)\
            .filter(
                and_(
                    Sale.id == sale_id,
                    Sale.shop_id == shop_id
                )
            )\
            .options(
                db.joinedload(Sale.cart_items)
                .joinedload(CartItem.product)
            )\
            .first()

    @staticmethod
    def create_sale(
        shop_id: int,
        user_id: int,
        cart_items: List[Dict],
        payment_method: str,
        customer_name: Optional[str] = None
    ) -> Sale:
        """
        Create a new sale record using CartItem as sale items
        """
        # Safely calculate subtotal
        subtotal = float(sum(float(item['price']) * float(item['quantity']) for item in cart_items))
        tax = float(0)  # or float(subtotal * tax_rate) if needed
        total = float(subtotal + tax)

        # Create sale
        sale = Sale(
            shop_id=shop_id,
            user_id=user_id,
            total=total,
            subtotal=subtotal,
            tax=tax,
            payment_method=payment_method,
            customer_name=customer_name
        )
        db.session.add(sale)
        db.session.flush()

        # Add cart items as sale items
        for item in cart_items:
            quantity = float(item['quantity'])
            unit_price = float(item['price'])

            cart_item = CartItem(
                shop_id=shop_id,
                product_id=item['product_id'],
                quantity=quantity,
                unit_price=unit_price,
                total_price=unit_price * quantity,
                sale_id=sale.id
            )
            db.session.add(cart_item)

        return sale

