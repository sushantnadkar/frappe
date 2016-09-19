# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""
# Integrating RazorPay

### Validate Currency

Example:

	from frappe.integration_broker.doctype.integration_service.integration_service import get_integration_controller

	controller = get_integration_controller("Razorpay", setup=False)
	controller().validate_transaction_currency(currency)

### 2. Redirect for payment

Example:

	payment_details = {
		"amount": 600,
		"title": "Payment for bill : 111",
		"description": "payment via cart",
		"reference_doctype": "Payment Request",
		"reference_docname": "PR0001",
		"payer_email": "NuranVerkleij@example.com",
		"payer_name": "Nuran Verkleij",
		"order_id": "111",
		"currency": "INR"
	}

	from frappe.integration.razorpay import get_checkout_url

	# Redirect the user to this url
	url = controller().get_payment_url(**payment_details)


### 3. On Completion of Payment

Write a method for `on_payment_authorized` in the reference doctype

Example:

	def on_payment_authorized(payment_status):
		# this method will be called when payment is complete


##### Notes:

payment_status - payment gateway will put payment status on callback.
For razorpay payment status is Authorized

"""
from __future__ import unicode_literals
import frappe
from frappe import _
import urllib, json
from frappe.utils import get_url, call_hook_method
from frappe.integration_broker.integration_controller import IntegrationController

class Controller(IntegrationController):
	service_name = 'Razorpay'
	parameters_template = [
		{
			"label": "API Key",
			'fieldname': 'api_key',
			'fieldtype': "Data",
			'reqd': 1
		},
		{
			"label": "API Secret",
			'fieldname': 'api_secret',
			'fieldtype': "Password",
			'reqd': 1
		}
	]

	# do also changes in razorpay.js scheduler job helper
	scheduled_jobs = [
		{
			"all": [
				"frappe.integrations.razorpay.capture_payment"
			]
		}
	]

	js = "assets/frappe/js/integrations/razorpay.js"

	supported_currencies = ["INR"]

	def enable(self, parameters, use_test_account=0):
		call_hook_method('payment_gateway_enabled', gateway='Razorpay')
		self.parameters = parameters
		self.validate_razorpay_credentails()

	def validate_razorpay_credentails(self):
		razorpay_settings = self.get_settings()

		if razorpay_settings.get("api_key"):
			try:
				self.get_request(url="https://api.razorpay.com/v1/payments",
					auth=(razorpay_settings.api_key, razorpay_settings.api_secret))
			except Exception:
				frappe.throw(_("Seems API Key or API Secret is wrong !!!"))

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(_("Please select another payment method. {0} does not support transactions in currency '{1}'").format(self.service_name, currency))

	def get_payment_url(self, **kwargs):
		return get_url("./integrations/razorpay_checkout?{0}".format(urllib.urlencode(kwargs)))

	def get_settings(self):
		if hasattr(self, "parameters"):
			return frappe._dict(self.parameters)

		custom_settings_json = frappe.db.get_value("Integration Service", "Razorpay", "custom_settings_json")

		if custom_settings_json:
			return frappe._dict(json.loads(custom_settings_json))

	def create_request(self, data):
		self.data = frappe._dict(data)

		try:
			self.integration_request = super(Controller, self).create_request(self.data, "Host", \
				self.service_name)
			return self.authorize_payment()

		except Exception:
			return{
				"redirect_to": frappe.redirect_to_message(_('Server Error'), _("Seems issue with server's razorpay config. Don't worry, in case of failure amount will get refunded to your account.")),
				"status": 401
			}

	def authorize_payment(self):
		"""
		An authorization is performed when user’s payment details are successfully authenticated by the bank.
		The money is deducted from the customer’s account, but will not be transferred to the merchant’s account
		until it is explicitly captured by merchant.
		"""

		settings = self.get_settings()

		if self.integration_request.status != "Authorized":
			resp = self.get_request("https://api.razorpay.com/v1/payments/{0}"
				.format(self.data.razorpay_payment_id), auth=(settings.api_key,
					settings.api_secret))

			if resp.get("status") == "authorized":
				self.integration_request.db_set('status', 'Authorized', update_modified=False)
				self.flags.status_changed_to = "Authorized"

		if self.flags.status_changed_to == "Authorized":
			if self.data.reference_doctype and self.data.reference_docname:
				redirect_to = frappe.get_doc(self.data.reference_doctype, self.data.reference_docname).run_method("on_payment_authorized", self.flags.status_changed_to)

			return {
				"redirect_to": redirect_to or "payment-success",
				"status": 200
			}

def capture_payment(is_sandbox=False, sanbox_response=None):
	"""
		Verifies the purchase as complete by the merchant.
		After capture, the amount is transferred to the merchant within T+3 days
		where T is the day on which payment is captured.

		Note: Attempting to capture a payment whose status is not authorized will produce an error.
	"""
	controller = frappe.get_doc("Integration Service", "Razorpay")
	settings = controller.get_parameters()
	for doc in frappe.get_all("Integration Request", filters={"status": "Authorized",
		"integration_request_service": "Razorpay"}, fields=["name", "data"]):
		try:
			if is_sandbox:
				resp = sanbox_response
			else:
				data = json.loads(doc.data)
				resp = controller.post_request("https://api.razorpay.com/v1/payments/{0}/capture".format(data.get("razorpay_payment_id")),
					auth=(settings["api_key"], settings["api_secret"]), data={"amount": data.get("amount")})

			if resp.get("status") == "captured":
				frappe.db.set_value("Integration Request", doc.name, "status", "Completed")

		except Exception:
			doc = frappe.get_doc("Integration Request", doc.name)
			doc.status = "Failed"
			doc.error = frappe.get_traceback()

@frappe.whitelist(allow_guest=True, xss_safe=True)
def get_checkout_url(**kwargs):
	try:
		return Controller().get_payment_url(**kwargs)
	except Exception:
		frappe.respond_as_web_page(_("Something went wrong"),
			_("Looks like something is wrong with this site's Razorpay configuration. Don't worry! No payment has been made."),
			success=False,
			http_status_code=frappe.ValidationError.http_status_code)
