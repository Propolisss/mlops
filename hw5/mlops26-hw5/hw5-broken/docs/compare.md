# Базовая модель против адаптера

Адаптер: `models/adapter_all_layers`. Генерация жадная, до 256 токенов.
Тикеты — из val, в подвыборку обучения и оценки не попадали.

Валидный JSON: база 5 из 5, адаптер 5 из 5.

Совпадений с разметкой из 5, по полям — queue: база 0, адаптер 1 · type: база 0, адаптер 2 · priority: база 1, адаптер 2.

| # | Разметка датасета | База | Адаптер |
|---|---|---|---|
| 1 | IT Support / Incident / medium | Connectivity Problem / Network / High | Network and Security / Incident / medium |
| 2 | Returns and Exchanges / Change / high | Refund and Replacement Request / Request / High | Customer Service / Request / medium |
| 3 | Service Outages and Maintenance / Problem / low | SaaS Platform Access Difficulties / Incident / Medium | Technical Support / Incident / medium |
| 4 | Billing and Payments / Problem / high | payment-processing / billing-system / high | Billing and Payments / Incident / high |
| 5 | Product Support / Request / low | Support / Pricing Plan Inquiry / Medium | Customer Service / Request / medium |

## 1. Connectivity Problem

**База:**

> { /   "queue": "Connectivity Problem", /   "type": "Network", /   "priority": "High", /   "tags": ["Network Disruption", "Mesh Network", "PyTorch", "Elasticsearch", "Smart Home Sensors"], /   "reply": "The healthcare organization has reported sporadic network disruptions affecting their systems. Network devices were rebooted and Red Enterprise Linux systems were updated. Please confirm if there are any specific issues or recommendations for resolution." / }

**Адаптер:**

> {"queue": "Network and Security", "type": "Incident", "priority": "medium", "tags": ["Network", "Disruption", "Disruption", "Disruption", "Disruption"], "reply": "We are investigating the sporadic network disruptions affecting your healthcare organization. We will contact you at <tel_num> to discuss the issue further and provide a solution."}

## 2. Request for Refund and Replacement

**База:**

> { /   "queue": "Refund and Replacement Request", /   "type": "Request", /   "priority": "High", /   "tags": ["device", "software", "refund", "replacement"], /   "reply": "Please find attached your purchase receipt for your records. Kindly inform me of the necessary steps to address this matter promptly." / }

**Адаптер:**

> {"queue": "Customer Service", "type": "Request", "priority": "medium", "tags": ["Product", "Refund", "Replacement", "Disruption", "Disruption", "Disruption"], "reply": "Dear [Name], thank you for reaching out to us regarding the faulty devices and software licenses you received. We appreciate your concern and would like to assist you in resolving this matter. To facilitate this, we need to review your purchase receipt and provide you with a detailed account of the issue. Please allow us to contact you at <tel_num> to discuss the matter further. We will schedule a call at your convenience to address the problem and provide a suitable replacement."}

## 3. Recent Reports of Sporadic Access Difficulties

**База:**

> { /   "queue": "SaaS Platform Access Difficulties", /   "type": "Incident", /   "priority": "Medium", /   "tags": ["SaaS", "Server Updates", "Access Difficulties"], /   "reply": "Please continue monitoring the situation and keep us updated as necessary." / }

**Адаптер:**

> {"queue": "Technical Support", "type": "Incident", "priority": "medium", "tags": ["Performance", "Outage", "IT", "Tech Support"], "reply": "We will investigate the sporadic access issues with the SaaS platform. Please provide more details about the recent server updates and the specific error messages encountered. We will schedule a call to discuss the problem and determine the best course of action to resolve it."}

## 4. Error Detected in Payment Processing System

**База:**

> { /   "queue": "payment-processing", /   "type": "billing-system", /   "priority": "high", /   "tags": ["payment-processing", "billing-system", "software-update"], /   "reply": "Please confirm the billing system issue and provide the necessary details for the support team to resolve it promptly." / }

**Адаптер:**

> {"queue": "Billing and Payments", "type": "Incident", "priority": "high", "tags": ["Billing", "Payment", "Disruption", "Tech Support", "Feedback"], "reply": "We will investigate the billing system issue and provide a solution to resolve the payment processing problem. Please provide more details about the error message and the steps you have taken to address the issue. We will contact you at <tel_num> to discuss the matter further."}

## 5. Details of Pricing Plans

**База:**

> { /   "queue": "Support", /   "type": "Pricing Plan Inquiry", /   "priority": "Medium", /   "tags": ["Pricing Plans", "Project Management SaaS", "Trial Options", "Discounts"], /   "reply": "I'll be able to provide details on the available pricing plans and trial options. Please let me know if you have any questions about the features, benefits, or discounts." / }

**Адаптер:**

> {"queue": "Customer Service", "type": "Request", "priority": "medium", "tags": ["Feature", "Pricing", "Trial", "Discount", "Feature", "Pricing", "Trial", "Discount"], "reply": "I am pleased to provide details on the available pricing plans and trial options for the project management SaaS offerings. Please let me know a convenient time to discuss further."}
