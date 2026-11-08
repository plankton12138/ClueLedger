/** 订阅支付：直接打开支付宝电脑网站收银台（page pay 不是扫码产品，二维码需由收银台自己生成）。 */

interface Props {
  url: string;
}

export default function SubscriptionPay({ url }: Props) {
  return (
    <section className="billing-pay">
      <a
        className="ui-button"
        data-variant="primary"
        data-size="compact"
        href={url}
        target="_blank"
        rel="noreferrer"
      >
        去支付宝收银台付款
      </a>
      <p className="ui-field-hint">
        浏览器没有自动打开收银台，请点上方按钮继续；收银台里可用支付宝登录或扫码付款。
      </p>
    </section>
  );
}
