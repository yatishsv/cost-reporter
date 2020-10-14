import boto3
from datetime import datetime, timedelta
import slack_sender
import stacked_bar
import os
import logging


def get_daily_cost(days):
    """ Returns the spend for each service together with the days
    """
    today = datetime.today().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # Calculate dates
    dates = []
    for i in range(0, days):
        day = (datetime.today() - timedelta(days=i)).strftime("%d")
        dates.append(day)
    dates.reverse()

    # Get daily spend
    client = boto3.client('ce')
    results = client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': today
        },
        Granularity='DAILY',
        Metrics=[
            'AmortizedCost',
        ],
        GroupBy=[
            {
                'Type': 'DIMENSION',
                'Key': 'SERVICE'
            }
        ]
    )["ResultsByTime"]

    # Prep the data for the graphing
    # Get the list of services
    services = [x["Keys"][0] for x in results[0]["Groups"]]
    daily_cost = {service: [] for service in services}
    for service in services:
        for day in results:
            found_service = False
            for group in day["Groups"]:
                if service in group["Keys"]:
                    daily_cost[service].append(float(group["Metrics"]["AmortizedCost"]["Amount"]))
                    found_service = True
                    break
            # Fill up a 0 if service doesnt have cost for that day
            if not found_service:
                daily_cost[service].append(0)

    return (daily_cost, dates)


def trigger_notification(graph_data):
    should_send = True

    total_cost_today = 0
    for service in graph_data.keys():
        total_cost_today += graph_data[service][-1]
    logging.info(f"Total cost today: {total_cost_today}")

    only_notify_on_increase = bool(os.environ["ONLY_NOTIFY_ON_INCREASE"])
    if only_notify_on_increase:
        total_cost_yesterday = 0
        for service in graph_data.keys():
            total_cost_yesterday += graph_data[service][-2]
        logging.info(f"Total cost yesterday: {total_cost_yesterday}")

        if total_cost_today < total_cost_yesterday:
            should_send = False

    min_daily_cost = int(os.environ["MIN_DAILY_COST"])
    if min_daily_cost > total_cost_today:
        should_send = False
        logging.info("Minimal daily cost not exceeded!")
    else:
        logging.info("Minimal daily cost exceeded!")

    return should_send


def lambda_handler(event, context):
    days = int(os.environ["DAYS"])

    (daily_cost, dates) = get_daily_cost(days)

    # Find the biggest spenders
    total_cost = []
    for service in daily_cost.keys():
        total_cost.append((service, sum(daily_cost[service])))
    total_cost = sorted(total_cost, key=lambda x: x[1], reverse=True)

    top_5 = [x[0] for x in total_cost][0:5]
    the_rest = [x[0] for x in total_cost][5:]
    logging.info(top_5)

    graph_data = {}
    # add top-5
    for service in top_5:
        graph_data[service] = daily_cost[service]
    # add the rest
    graph_data["Other"] = [0 for x in range(days)]
    for service in the_rest:
        graph_data["Other"] = [x + y for x, y in zip(graph_data["Other"], daily_cost[service])]

    stacked_bar.draw_bars(graph_data, dates, f"{os.environ['TITLE']} (last {days} days)")

    # Send the report if necessary
    if trigger_notification(graph_data):
        logging.info("Sending message")
        slack_sender.send_image("/tmp/image.png", os.environ["TARGET_CHANNEL"])