# Import all the necessary libraries
from square.client import Client
import vertexai
from vertexai.language_models import ChatModel, InputOutputTextPair
from google.oauth2 import service_account
import streamlit as st
import qrcode
import random
import requests
import os
from time import sleep
import pickle
import ast
import datetime
import json
from bokeh.models.widgets import Button
from bokeh.models import CustomJS
from streamlit_bokeh_events import streamlit_bokeh_events
from io import BytesIO
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import smtplib


# Website configurations
st.set_page_config(page_title="Inco",page_icon="🧾")

# Helper Functions

# Setup Square Connection Function
@st.cache_resource # Connection Pooling
def create_square_connection():
	client = Client(
					access_token = st.secrets["square_key"],
					environment = 'sandbox')

	return client


# Connect to Square Sandbox
square_client = create_square_connection()
square_location_id = "L08D3V31BNM3V"

# Square APIs
# --------------
# Retrieve Catalog Information
def return_catalog():
	res = square_client.catalog.list_catalog()
	return res


# Use Google VertexAI Chat Model 
def vertexAI_chat(context, prompt, ioarray, selected_temperature = 0):
	credentials = service_account.Credentials.from_service_account_info(st.secrets["gcs"])
	vertexai.init(project="square-hackathon-401819", location="us-central1", credentials = credentials)
	chat_model = ChatModel.from_pretrained("chat-bison")
	# subscription plan prompt: now take an imaginary customer . populate with 2 orders and create a dynamic personalized subscription plan for that user which will be a win-win for customer and business. give your reasoning
	parameters = {
	    "candidate_count": 1,
	    "max_output_tokens": 2000,
	    "temperature": selected_temperature,
	    "top_p": 0.8,
	    "top_k": 40
	}
	chat = chat_model.start_chat(context = context, examples = [InputOutputTextPair(input_text=ioarray[0], output_text = ioarray[1])])
	response = chat.send_message(f"{prompt}", **parameters)
	print(f"{response.text}")
	return response


# Load Coupons Data stored locally
with open("coupons.pickle","rb") as file:
    coupons_dictionary = pickle.load(file)


# Delete Existing Catalog
def delete_catalog_item(objectid):
	res = square_client.catalog.delete_catalog_object(
			  object_id = objectid
			)

	return res


# Create new Customer if doesn't exist
@st.cache_data
def create_new_customer(email_id):

	st.write("Checking customer details")
	
	st.session_state["latest_customer_email"] = email_id

	# First let us check if cutomer already exists to ensure we do not create duplicates
	result = square_client.customers.search_customers(
		body = {
			    "limit": 1,
			    "query": {
			      "filter": {
			        "email_address": {
			          "exact": email_id
			        }
			      }
			    }
			  }
			)
  

	result_body = result.body

	# Customer with email already exists
	if len(result_body) != 0:
		customer_id = result_body["customers"][0]["id"]
	# No existing customer so create profile
	else:
		new_cutomer_result = square_client.customers.create_customer(
			body = {
				"email_address": f"{email_id}"
			}) 

		customer_id = new_customer_result.body["customer"]["id"]

	return customer_id	
			

# Create new Order
@st.cache_data
def create_new_order(given_order_details, customer_id):
	#structured_catalog = vertexAI_chat(cat_context, seller_entered_catalog, ioarray).text
	order_context = """Give output in given format. You have to determine correct items that were ordered calculate base_price_money values based on if the coupon condition is satisfied."""
	
	if customer_id in coupons_dictionary:
		current_coupon = coupons_dictionary[customer_id]
	else:
		current_coupon = "No Coupon Available"

	required_catalog = coupons_dictionary["customer_catalog"]
	#st.write(required_catalog)		

	order_info = f"Catalog:\n{required_catalog}\n\nCoupon Condition:\n{current_coupon}\n\nOrder details:\n{given_order_details}"

	ioarray = ["""Catalog:
				Burger: Regular 5$, Large 9$
				Fanta: Small 3$, Large 7$

				Coupon Condition:
				Decrease 3$ on Large Burger base_price if a Large Fanta is also bought

				Order details: 1 large Burger and 2 large Fantas""",

				"""{"line_items":[{"name":"Burger","quantity":"1","note":"Coupon applied: You saved 3$ on Large Burger since you purchased a Large Fanta!","variation_name":"Regular","item_type":"ITEM","base_price_money":{"amount":6,"currency":"USD"}},{"name":"Fanta","quantity":"2","note":"","variation_name":"Large","item_type":"ITEM","base_price_money":{"amount":7,"currency":"USD"}}]}
				"""]

	structured_order = vertexAI_chat(order_context, order_info, ioarray, 0.4).text
	st.write(structured_order)
	required_json = json.loads(str(structured_order))["line_items"]
	st.write(required_json)
	#["line_items"]

	if customer_id in coupons_dictionary:
		del coupons_dictionary[customer_id]

	order_request = square_client.orders.create_order(
					body = {
						"order": {
								"location_id": "L08D3V31BNM3V",
								"customer_id": customer_id,
								"line_items": required_json
									},
						"idempotency_key": str(datetime.datetime.now().time())			
							} 
													)

	order_id = order_request.body["order"]["id"]
	st.write(order_request.body)
	st.session_state["latest_order_id"] = order_id
	st.session_state["latest_customer_id"] = customer_id
	st.session_state["latest_order_details"] = given_order_details
	return order_id

#delete_catalog_item("L6EHPLCKSNKUQQMRVWZMAVGK")


# Create Invoice and Publish it
@st.cache_data
def create_and_publish_invoice(order_id, customer_id):
	invoice_request = square_client.invoices.create_invoice(
						body = {
							"invoice": {
								"order_id": order_id,
								"primary_recipient": {
									"customer_id": customer_id
								},
								"payment_requests": [
									{
										"request_type": "BALANCE",
										"due_date": (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
										"automatic_payment_source": "NONE"
									}
								],
								"delivery_method": "SHARE_MANUALLY",
								"accepted_payment_methods": {
									"card": True
								}
							}	
						})

	invoice_request_body = invoice_request.body
	invoice_id = invoice_request_body["invoice"]["id"]
	invoice_version = invoice_request_body["invoice"]["version"]

	# Publish Created Invoice
	publish_invoice_request = square_client.invoices.publish_invoice(
									invoice_id = invoice_id,
									body = {
										"version": invoice_version
									}
								)

	# Get Invoice Public URL
	invoice_public_url = publish_invoice_request.body["invoice"]["public_url"]
	return invoice_public_url


# Generate New Coupon
@st.cache_data
def new_coupon(order_details):
	customer_id = st.session_state["latest_customer_id"]
	#structured_order = vertexAI_chat(order_context, order_info, ioarray)
	new_coupon_context = """You will be given details about a Shop catalog and a user's latest order. Based on that suggest a new coupon that can be given to the user such that it incentives the user to shop again or try a new product. Follow structure of given examples."""
	ioarray = ["""Shop Catalog:
				Pizza: Regular is 6$, Large is 9$
				Coke: Small costs 3$, Large would be 7$ and a medium one for 5$ 
				
				Order details: 1 large pizza and 2 large cokes
				""",
				"""
				{"coupon_item":"Large Pizza","coupon_desc":"Decrease 2$ on Large pizza base_price if garlic bread is also purchased"}
				"""]
	temperature = 0.2
	message = f"""Shop Catalog:\n{coupons_dictionary["customer_catalog"]}\n\nOrder details: {order_details}"""
	vertex_chat_output = vertexAI_chat(new_coupon_context, message, ioarray, temperature).text	
	required_json = json.loads(str(vertex_chat_output))
	item_name = required_json["coupon_item"]
	coupon_desc = required_json["coupon_desc"]
	prefix = "https://incosquare.streamlit.app/"
	coupon_link = f"{prefix}?customer_id={customer_id}&coupon_condition={coupon_desc}" 
	return (coupon_link, item_name, coupon_desc)	



# Check if Coupon Link
query_params = st.experimental_get_query_params()
if "customer_id" in query_params:
	coupons_dictionary["customer_id"] = query_params["coupon_condition"]
	st.success("Your Coupon has been claimed! It will applied on the next eligible order. Thanks!")
	st.balloons()
	sleep(10)


# Generate QR code -- simple link encoded in the code
def QR_code_maker(link, custom_image = None):
	qr = qrcode.QRCode(
						version = 1,
						error_correction = qrcode.constants.ERROR_CORRECT_H,
						box_size = 10,
						border = 4)

	qr.add_data(link)
	qr.make(fit = True)

	qr_image = qr.make_image(fill_color = "black", back_color = "white")

	# If image is given, open it
	if custom_image is not None:
		logo = Image.open('logo.png')
		logo_size = logo.size

		# Calculate position to place image - ERROR_CORRECT_H above can handle upto 30% error in QR
		img_w, img_h = img.size
		logo_w, logo_h = logo_size
		pos_w = int((img_w - logo_w) / 2)
		pos_h = int((img_h - logo_h) / 2)

		# Paste image to QR code
		img.paste(logo, (pos_w, pos_h))

		# Save final QR code image
		img.save('qr_with_logo.png') 


# Generate Custom Image and create QR code with it
@st.cache_data
def QR_code_genai(link, prompt):
	api_key = st.secrets["qr_key"]
	url = "https://api.segmind.com/v1/qrsd1.5-txt2img"

	# Request payload
	data = {
	  "prompt": prompt,
	  "negative_prompt": "bad, ugly, worst",
	  "scheduler": "dpmpp_2m",
	  "num_inference_steps": "20",
	  "guidance_scale": "7.5",
	  "control_scale": "1.8",
	  "control_start": "0.19",
	  "control_end": "1",
	  "samples": "1",
	  "seed": "19",
	  "size": "768",
	  "qr_text": link,
	  "invert": False,
	  "base64": False
	}

	response = requests.post(url, json=data, headers={'x-api-key': api_key})
	return response



# Page Elements
st.title("Inco 🧾")
st.sidebar.title("Inco 🧾")

selected_page = st.sidebar.radio("Pages",["Essential Business Info","Place Order","Coupons"], captions = ["Fill Details","Create Customer Order","Send Invoice & Incentives"])


# Place Order Page Logic
if selected_page == "Place Order":
	st.header("Place Order")
	st.divider()

	customer_email = st.text_input("Enter Customer Email","")

	# Voice Input
	stt_button = Button(label="Click and Speak Order Details", width=690, height=40) 

	stt_button.js_on_event("button_click", CustomJS(code="""
	    var recognition = new webkitSpeechRecognition();
	    recognition.continuous = false;
	    recognition.interimResults = false;
	    
	    recognition.start();

	    recognition.onresult = function (e) {
	        var value = "";
	        for (var i = e.resultIndex; i < e.results.length; ++i) {
	            if (e.results[i].isFinal) {
	                value += e.results[i][0].transcript;
	            }
	        }
	        if ( value != "") {
	            document.dispatchEvent(new CustomEvent("GET_TEXT", {detail: value}));
	        }
	    }    
	    """))

	result = streamlit_bokeh_events(
	    stt_button,
	    events="GET_TEXT",
	    key="listen",
	    refresh_on_update=False,
	    override_height=75,
	    debounce_time=0)

	st.divider()


	if result:
	    if "GET_TEXT" in result:

	    	spoken_order = result.get("GET_TEXT")
	    	
	    	st.caption(f'You said: {spoken_order}')

	    	if customer_email == "":
	    		st.warning("Please enter Email and Speak Order Details")

	    	else:
	    		with st.spinner("Creating New Order | Talking to Square"):
		    		po_customer_id = create_new_customer(customer_email)
				st.session_state["latest_customer_email"] = customer_email
				po_order_id = create_new_order(spoken_order, po_customer_id)
				st.success("Order Placed. Please head to Coupons Page")
				

# Coupons Page Logic
elif selected_page == "Coupons":
	st.header("Invoice and Coupons")

	if "latest_order_details" not in st.session_state:
		st.warning("Please Place an Order first")

	else:
		to_email = st.session_state["latest_customer_email"]
		from_email = "sharanbabu2001@gmail.com"
		email_password = st.secrets["email_password"]
		order_details = st.session_state["latest_order_details"]

		with st.spinner("Creating Invoice"):
			order_id = st.session_state["latest_order_id"]
			customer_id = st.session_state["latest_customer_id"]
			invoice_payment_url = create_and_publish_invoice(order_id, customer_id)

		st.success("Invoice Successfully Created")

		coupon_link, item_name, coupon_desc = new_coupon(order_details)
		coupon_link = coupon_link.replace(" ","%20")
		with st.spinner("Generating Special Coupon Scan Code"):
			image_response = QR_code_genai(coupon_link, item_name).content
		#st.write(f"Order Taken: {order_details}")
		coupon_text_template = f"""We also have a special coupon for you! You can claim it by clicking the link below or scanning the Image below: <a href={coupon_link}>Coupon Link</a>"""
		email_draft_content = f"""Hi there,\nHere is your payment link for your recent order of: {order_details}.\n<a href={invoice_payment_url}>Payment Link</a>\n{coupon_text_template}"""
		st.write(coupon_link)
		email_text = st.text_area("Email Body",email_draft_content, height = 200)
		st.caption("Links and Images will be formatted and placed automatically")

		st.divider()
		st.write("Coupon:")
		st.write(coupon_desc)
		st.image(image_response, width = 300)
		st.divider()

		coupon_selection = st.radio("Do you want to send coupon?",["Yes","No"],horizontal = True)

		if st.button("Send Email"):
			#msg = MIMEMultipart('alternative')
			msg = MIMEMultipart('related')
			msg['Subject'] = "Your Invoice is here!"
			msg['From'] = from_email
			msg['To'] = to_email
			img = MIMEImage(image_response)
			img.add_header('Content-ID','<myimage>')
			body = MIMEText(f"""{email_text}\n\n""",'html')
			msg.attach(body)

			if coupon_selection == "Yes":
				msg.attach(img)

			with st.spinner("Sending Email"):
				s = smtplib.SMTP("smtp.gmail.com",587)
				s.starttls()
				s.login(from_email, email_password)
				s.sendmail(from_email, to_email, msg.as_string())
				s.quit()

			st.success("Email Sent")


		



# Essential Business Info Page Logic
elif selected_page == "Essential Business Info":
	st.header("Essential Business Info")

	with st.expander("How it works?"):
		st.markdown("If you are yet to create a catalog, you can use the textfield below to enter your Menu/Catalog Items in just plain English. **Inco** then uses the power of **Google AI Chat Bison Model** to convert your freeform text into structured format which can then be successfully sent to the **Square API**! This simplifies the process for you and makes it so that you can simply enter the Catalog Items.")
		st.image("images/1.JPG")
		st.markdown("""
						**Sample Catalog:**\n
					_Pizza_: Regular is 6\$, Large is 9\$\n
					_Coke_: Small costs 3\$, Large would be 7\$ and a medium one for 5\$\n
					_Garlic Bread_ : 3\$
					""")

	# Retrieve Seller Catalog
	with st.spinner("Fetching Catalog Details..."):
		result = return_catalog()
	#st.write(result)
	if result.is_success():
		if len(result.body) != 0:
			st.info("Please create a Catalog")

			seller_entered_catalog = st.text_area("My Catalog","",placeholder="Products, Taxes, combos info...")

			if st.button("Submit"):
				st.divider()
				if seller_entered_catalog:
					# Send To Google Vertex Chat Model
					with st.spinner("Structuring Catalog and talking to Square"):
						cat_context = """Give output in specified format for given list of items.
											"type" can be item, tax or discount"""
						
						ioarray = ["""burger 5$
									  cheese pizza 6$""",

									  '{"idempotency_key": "unique_key","batches": [{"objects": [{"type": "ITEM","id": "#burger","item_data": {"name": "burger","variations": [{"type": "ITEM_VARIATION","id": "#burger-regular","item_variation_data": {"item_id": "#burger","name": "regular","pricing_type": "FIXED_PRICING","price_money": {"amount": 5,"currency": "USD"}}}]}},{"type": "ITEM","id": "#cheese_pizza","item_data": {"name": "cheese pizza","variations": [{"type": "ITEM_VARIATION","id": "#cheese_pizza-regular","item_variation_data": {"item_id": "#cheese_pizza","name": "regular","pricing_type": "FIXED_PRICING","price_money": {"amount": 6,"currency": "USD"}}}]}}]}]}']				
						
						structured_catalog = vertexAI_chat(cat_context, seller_entered_catalog, ioarray).text
						#st.write(structured_catalog)
						#structured_catalog.replace("\n","").replace("\t","")
						#structured_catalog = """{"idempotency_key": "unique_key","batches": [{"objects": [{"type": "ITEM","id": "#kale","item_data": {"name": "kale","variations": [{"type": "ITEM_VARIATION","id": "#kale-regular","item_variation_data": {"item_id": "#kale","name": "regular","pricing_type": "FIXED_PRICING","price_money": {"amount": 9,"currency": "USD"}}}]}}]}]}"""
						
						

					# Parse and convert user input catalog into structured format
					#parsed_catalog = ast.literal_eval(structured_catalog)
					parsed_catalog = json.loads(str(structured_catalog))
					#st.write(parsed_catalog)

					parsed_catalog["idempotency_key"] = str(datetime.datetime.now().time())

					# upload to square
					upload_catalog_status = square_client.catalog.batch_upsert_catalog_objects(body = parsed_catalog)
					#st.write(upload_catalog_status)

					if upload_catalog_status.is_success():
						coupons_dictionary["customer_catalog"] = seller_entered_catalog
						st.success("Catalog Uploaded Successfully!")

					elif result.is_error():
						st.error("Some error occurred. Please refresh page and try again")	

				else:
					st.warning("Please fill catalog details above")	


		else:
			st.info("Catalog already exists. Here it is:")
			with st.expander("Expand to see Catalog Entered"):
				st.text(coupons_dictionary["customer_catalog"])
				st.divider()
				st.text("Catalog in JSON format")
				st.json(result.body, expanded = True)
	else:
		st.error("Error Fetching Details. Please Reload page")

		

# Save Coupon Data Changes
with open("coupons.pickle","wb") as file:
	pickle.dump(coupons_dictionary, file)
